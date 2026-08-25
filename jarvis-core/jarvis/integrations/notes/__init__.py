"""notes — markdown files you own, that Jarvis can read and write.

A note is a file::

    <config>/notes/boiler-serviced.md

        ---
        title: Boiler serviced
        tags: [house, maintenance]
        created: 2026-08-24T10:00:00Z
        updated: 2026-08-24T10:00:00Z
        ---

        Pressure was 1.2 bar cold. Next service due March.
        See [[heating]] for the flow temperature.

Markdown on disk, not rows in a database, and that is the whole design. The
notes are readable in any editor, syncable with any tool, greppable, and
survivable: if Jarvis is uninstalled tomorrow they are still a folder of
markdown. The SQLite index beside them (`.storage/notes.db`, FTS5) is
*derived* — delete it and it is rebuilt from the files on the next start.

## Notes versus memory

They are different things and confusing them is why this integration exists.

* `memory` holds **facts about the user**, one line each, injected into every
  system prompt. It is small, bounded, and about *them*.
* `notes` hold **documents** — a research report, a recipe, a list, minutes of
  a phone call. They are as long as they need to be, and none of them is in the
  prompt unless the model goes and reads one.

Research used to write its reports into memory. A four-page report as a
"remembered note" pushed the user's actual preferences out of a bounded store
and put four pages of prose into every prompt. Reports are notes.

Configuration (every key optional)::

    notes:
      path: notes           # relative to the config directory
      max_bytes: 262144     # a single note's ceiling

Services
    ``notes.create``  (title, body, tags) → the note
    ``notes.append``  (id/title, text)    → the note
    ``notes.search``  (query, tag, limit) → ``{"results": [...]}``
    ``notes.read``    (id/title)          → the note, body included
    ``notes.delete``  (id)                → ``{"deleted": bool}``

LLM tools: ``note_create``, ``note_append``, ``note_search`` (which reads one
whole note when given an id — one tool rather than two, because every tool
costs context in every turn).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "notes"
DEPENDENCIES = ["llm"]

DEFAULT_PATH = "notes"
#: One note's ceiling. Generous — a research report is a few tens of KB — and
#: finite, because a note is written by a model and a runaway loop should cost
#: a quarter of a megabyte rather than a disk.
DEFAULT_MAX_BYTES = 256 * 1024
MAX_TITLE = 120
MAX_TAGS = 12
#: What a search returns unless asked otherwise.
DEFAULT_LIMIT = 10
#: The index. Derived from the files, so it is safe to delete.
INDEX_FILE = "notes.db"

DATA_STORE = "notes"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_LINK_RE = re.compile(r"\[\[([^\]|]{1,120})(?:\|[^\]]*)?\]\]")


def slugify(title: str) -> str:
    """A filename from a title. Stable, so writing the same title twice edits."""
    slug = _SLUG_RE.sub("-", str(title or "").strip().lower()).strip("-")
    return (slug or "note")[:80]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_tags(value: Any) -> list[str]:
    if value is None:
        return []
    raw = re.split(r"[,\s]+", value) if isinstance(value, str) else list(value or [])
    out: list[str] = []
    for item in raw:
        tag = _SLUG_RE.sub("-", str(item).strip().lower()).strip("-")
        if tag and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


def links_in(body: str) -> list[str]:
    """Every `[[wiki link]]` in a body, as slugs.

    The link target is written as a title and resolved as a slug, so
    `[[Boiler serviced]]`, `[[boiler-serviced]]` and `[[BOILER SERVICED]]` are
    the same note — which is what somebody typing a link expects, and what
    makes back-links work without a rename breaking them.
    """
    return list(dict.fromkeys(slugify(match.group(1)) for match in _LINK_RE.finditer(body or "")))


@dataclass
class Note:
    slug: str
    title: str
    body: str = ""
    tags: list[str] = field(default_factory=list)
    created: str = field(default_factory=now_iso)
    updated: str = field(default_factory=now_iso)
    path: Path | None = None
    #: Slugs this note points at, and the ones pointing back. Both derived.
    links: list[str] = field(default_factory=list)
    backlinks: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.slug

    def as_dict(self, body: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.slug,
            "slug": self.slug,
            "title": self.title,
            "tags": list(self.tags),
            "created": self.created,
            "updated": self.updated,
            "links": list(self.links),
            "backlinks": list(self.backlinks),
            "bytes": len(self.body.encode("utf-8")),
            "path": str(self.path) if self.path else "",
        }
        if body:
            out["body"] = self.body
        return out

    def to_markdown(self) -> str:
        front = yaml.safe_dump(
            {
                "title": self.title,
                "tags": list(self.tags),
                "created": self.created,
                "updated": self.updated,
            },
            sort_keys=False,
            allow_unicode=True,
        ).strip()
        return f"---\n{front}\n---\n\n{self.body.strip()}\n"


def parse_note(text: str, slug: str, path: Path | None = None) -> Note:
    """A note from its file. Frontmatter is optional — a plain markdown file
    dropped into the folder by a person is still a note, titled by its name."""
    raw = str(text or "")
    front: dict[str, Any] = {}
    body = raw
    if raw.lstrip().startswith("---"):
        stripped = raw.lstrip()[3:]
        end = stripped.find("\n---")
        if end != -1:
            try:
                parsed = yaml.safe_load(stripped[:end]) or {}
                if isinstance(parsed, dict):
                    front = parsed
                    body = stripped[end + 4 :]
            except yaml.YAMLError:
                # A file with a broken header is still a file somebody wrote.
                # Losing its text because its metadata is malformed would be
                # the worst possible trade.
                _LOGGER.warning("notes: %s has unreadable frontmatter; keeping the body", slug)
    title = str(front.get("title") or slug.replace("-", " ")).strip()[:MAX_TITLE]
    return Note(
        slug=slug,
        title=title or slug,
        body=body.strip(),
        tags=clean_tags(front.get("tags")),
        created=str(front.get("created") or now_iso()),
        updated=str(front.get("updated") or now_iso()),
        path=path,
        links=links_in(body),
    )


class NoteStore:
    """The folder, and the FTS index derived from it."""

    def __init__(self, jarvis: "Jarvis", root: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.jarvis = jarvis
        self.root = Path(root)
        self.max_bytes = int(max_bytes)
        self.notes: dict[str, Note] = {}
        self._db: sqlite3.Connection | None = None

    # --- the index --------------------------------------------------------
    @property
    def index_path(self) -> Path:
        return Path(self.jarvis.config_dir) / ".storage" / INDEX_FILE

    def _connect(self) -> sqlite3.Connection:
        if self._db is None:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.index_path)
            self._db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes USING fts5("
                "slug UNINDEXED, title, body, tags)"
            )
            self._db.commit()
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def _reindex(self) -> None:
        """Rebuild the whole index from the files.

        Cheap (a few hundred notes), and it is what makes the index derived
        rather than authoritative: the files are the notes, and anything that
        edited them outside Jarvis — an editor, a sync client, `git checkout` —
        is picked up here rather than silently ignored.
        """
        db = self._connect()
        db.execute("DELETE FROM notes")
        db.executemany(
            "INSERT INTO notes (slug, title, body, tags) VALUES (?, ?, ?, ?)",
            [
                (note.slug, note.title, note.body, " ".join(note.tags))
                for note in self.notes.values()
            ],
        )
        db.commit()

    # --- loading ----------------------------------------------------------
    def load(self) -> int:
        self.notes.clear()
        if self.root.is_dir():
            for path in sorted(self.root.glob("*.md")):
                try:
                    note = parse_note(path.read_text(encoding="utf-8"), path.stem, path)
                except OSError as err:  # pragma: no cover - unreadable file
                    _LOGGER.warning("notes: could not read %s: %s", path, err)
                    continue
                self.notes[note.slug] = note
        self._resolve_backlinks()
        self._reindex()
        _LOGGER.info("notes: %d note(s) in %s", len(self.notes), self.root)
        return len(self.notes)

    def _resolve_backlinks(self) -> None:
        for note in self.notes.values():
            note.backlinks = []
        for note in self.notes.values():
            for target in note.links:
                other = self.notes.get(target)
                if other is not None and note.slug not in other.backlinks:
                    other.backlinks.append(note.slug)

    # --- reading ----------------------------------------------------------
    def get(self, key: str) -> Note | None:
        wanted = str(key or "").strip()
        if not wanted:
            return None
        return self.notes.get(wanted) or self.notes.get(slugify(wanted))

    def listing(self, tag: str = "", limit: int = 200) -> list[dict[str, Any]]:
        wanted = clean_tags(tag)
        notes = [
            note
            for note in self.notes.values()
            if not wanted or set(wanted) & set(note.tags)
        ]
        notes.sort(key=lambda n: n.updated, reverse=True)
        return [note.as_dict() for note in notes[:limit]]

    def search(self, query: str = "", tag: str = "", limit: int = DEFAULT_LIMIT) -> list[dict]:
        """Full text, through FTS5 — with a plain fallback.

        The fallback is not politeness: FTS5 has a query syntax, and a user
        searching for `boiler (march)` would otherwise get a syntax error
        instead of their note.
        """
        text = str(query or "").strip()
        if not text:
            return self.listing(tag=tag, limit=limit)
        db = self._connect()
        rows: list[tuple[str]] = []
        try:
            rows = db.execute(
                "SELECT slug FROM notes WHERE notes MATCH ? ORDER BY rank LIMIT ?",
                (text, limit * 4),
            ).fetchall()
        except sqlite3.OperationalError:
            # Word by word, not the whole string: somebody searching for
            # `boiler (march)` means "both of those words", and matching the
            # literal string would find nothing and look like an empty store.
            terms = [word for word in re.findall(r"[\w']+", text.lower()) if word]
            rows = [
                (note.slug,)
                for note in self.notes.values()
                if terms
                and all(
                    term in note.title.lower() or term in note.body.lower() for term in terms
                )
            ]
        wanted = clean_tags(tag)
        out: list[dict[str, Any]] = []
        for (slug,) in rows:
            note = self.notes.get(slug)
            if note is None:
                continue
            if wanted and not set(wanted) & set(note.tags):
                continue
            row = note.as_dict()
            row["excerpt"] = self._excerpt(note.body, text)
            out.append(row)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _excerpt(body: str, query: str, width: int = 160) -> str:
        lowered = body.lower()
        where = lowered.find(query.lower().split()[0]) if query.split() else -1
        if where < 0:
            return body[:width].strip()
        start = max(0, where - width // 3)
        return ("…" if start else "") + body[start : start + width].strip() + "…"

    # --- writing ----------------------------------------------------------
    def create(self, title: str, body: str = "", tags: Any = None,
               overwrite: bool = False) -> dict[str, Any]:
        clean_title = " ".join(str(title or "").split())[:MAX_TITLE]
        if not clean_title:
            return {"created": False, "error": "a note needs a title"}
        slug = slugify(clean_title)
        if slug in self.notes and not overwrite:
            return {
                "created": False,
                "error": f"a note called {self.notes[slug].title!r} already exists",
                "note": self.notes[slug].as_dict(),
            }
        text = str(body or "")
        if len(text.encode("utf-8")) > self.max_bytes:
            return {"created": False, "error": f"that note is over {self.max_bytes} bytes"}
        note = Note(
            slug=slug,
            title=clean_title,
            body=text.strip(),
            tags=clean_tags(tags),
            links=links_in(text),
        )
        self._write(note)
        return {"created": True, "note": note.as_dict()}

    def append(self, key: str, text: str) -> dict[str, Any]:
        note = self.get(key)
        if note is None:
            return {"appended": False, "error": f"no note called {key!r}"}
        addition = str(text or "").strip()
        if not addition:
            return {"appended": False, "error": "nothing to append"}
        body = f"{note.body}\n\n{addition}".strip()
        if len(body.encode("utf-8")) > self.max_bytes:
            return {"appended": False, "error": f"that note would exceed {self.max_bytes} bytes"}
        note.body = body
        note.links = links_in(body)
        note.updated = now_iso()
        self._write(note)
        return {"appended": True, "note": note.as_dict()}

    def update(self, key: str, body: str | None = None, title: str | None = None,
               tags: Any = None) -> dict[str, Any]:
        note = self.get(key)
        if note is None:
            return {"updated": False, "error": f"no note called {key!r}"}
        if body is not None:
            note.body = str(body).strip()
            note.links = links_in(note.body)
        if title:
            note.title = " ".join(str(title).split())[:MAX_TITLE]
        if tags is not None:
            note.tags = clean_tags(tags)
        note.updated = now_iso()
        self._write(note)
        return {"updated": True, "note": note.as_dict()}

    def delete(self, key: str) -> dict[str, Any]:
        note = self.get(key)
        if note is None:
            return {"deleted": False, "error": f"no note called {key!r}"}
        if note.path and note.path.is_file():
            note.path.unlink()
        self.notes.pop(note.slug, None)
        self._resolve_backlinks()
        self._reindex()
        return {"deleted": True, "id": note.slug}

    def _write(self, note: Note) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        note.path = self.root / f"{note.slug}.md"
        note.updated = now_iso()
        # Atomically: a note half-written by a crash is a note somebody loses.
        temp = note.path.with_suffix(".md.tmp")
        temp.write_text(note.to_markdown(), encoding="utf-8")
        temp.replace(note.path)
        self.notes[note.slug] = note
        self._resolve_backlinks()
        self._reindex()


def _register_services(jarvis: "Jarvis", store: NoteStore) -> None:
    async def create(call: Any) -> Any:
        data = call.data or {}
        return store.create(
            title=str(data.get("title") or ""),
            body=str(data.get("body") or data.get("text") or ""),
            tags=data.get("tags"),
            overwrite=bool(data.get("overwrite")),
        )

    async def append(call: Any) -> Any:
        data = call.data or {}
        return store.append(str(data.get("id") or data.get("title") or ""),
                            str(data.get("text") or data.get("body") or ""))

    async def read(call: Any) -> Any:
        data = call.data or {}
        note = store.get(str(data.get("id") or data.get("title") or ""))
        return {"note": note.as_dict(body=True)} if note else {"error": "no such note"}

    async def search(call: Any) -> Any:
        data = call.data or {}
        return {
            "results": store.search(
                str(data.get("query") or ""),
                str(data.get("tag") or ""),
                int(data.get("limit") or DEFAULT_LIMIT),
            )
        }

    async def delete(call: Any) -> Any:
        return store.delete(str((call.data or {}).get("id") or ""))

    async def reload(call: Any) -> Any:
        return {"loaded": store.load()}

    for name, handler in (
        ("create", create),
        ("append", append),
        ("read", read),
        ("search", search),
        ("delete", delete),
        ("reload", reload),
    ):
        jarvis.services.register(DOMAIN, name, handler, supports_response=True)


def _register_tools(jarvis: "Jarvis", store: NoteStore) -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        _LOGGER.debug("notes: no LLM tool registry; services registered without tools")
        return
    from ...llm.tools import schema_object

    async def tool_create(args: dict[str, Any], context: Any = None) -> Any:
        return store.create(
            title=str(args.get("title") or ""),
            body=str(args.get("body") or args.get("text") or ""),
            tags=args.get("tags"),
        )

    async def tool_append(args: dict[str, Any], context: Any = None) -> Any:
        return store.append(str(args.get("id") or args.get("title") or ""),
                            str(args.get("text") or ""))

    async def tool_read(args: dict[str, Any], context: Any = None) -> Any:
        note = store.get(str(args.get("id") or args.get("title") or ""))
        if note is None:
            return {
                "status": "error",
                "error": "no such note",
                "available": sorted(store.notes)[:20],
            }
        return {"status": "ok", "note": note.as_dict(body=True)}

    async def tool_search(args: dict[str, Any], context: Any = None) -> Any:
        """Search, or read one whole note when the caller names it.

        One tool rather than two. Every tool costs context in every turn —
        `tests/test_prompt_budget.py` is the thing that says how much — and
        "find it" and "open it" are the same intent from the model's side: it
        wants the note. Naming one returns its body; naming none returns the
        matches.
        """
        wanted = str(args.get("id") or args.get("title") or "")
        if wanted:
            return await tool_read({"id": wanted}, context)
        results = store.search(
            str(args.get("query") or ""),
            str(args.get("tag") or ""),
            int(args.get("limit") or DEFAULT_LIMIT),
        )
        if not results:
            # Say so plainly. A bare empty list invited the model to search
            # again with different words, and again — three rounds of a turn's
            # budget spent looking for something that was never written down.
            return {
                "status": "ok",
                "count": 0,
                "results": [],
                "message": (
                    "Nothing matched. Do not search the notes again with "
                    "different words — there is no note about this. If the "
                    "answer is on the web, look there (web_search, or "
                    "deep_research for anything needing several pages). "
                    "Otherwise say you have nothing written down."
                ),
                "notes_held": len(store.notes),
            }
        return {"status": "ok", "count": len(results), "results": results}

    registry.register(
        name="note_create",
        description=(
            "Write a note: a document kept on disk that can be found again. "
            "THIS is what 'note that…', 'make a note of…' and 'write that "
            "down' mean. Use it for anything worth keeping that is not a "
            "one-line fact about the user — what happened, a list, a report, "
            "instructions, minutes. "
            "(A standing fact about the user — 'I take my coffee black' — is "
            "`remember` instead: that goes into every future system prompt, "
            "and a document there would cost them context on every question.)"
        ),
        parameters=schema_object(
            {
                "title": {"type": "string", "description": "A short title."},
                "body": {"type": "string", "description": "The note, in markdown."},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            required=["title"],
        ),
        handler=tool_create,
        domain=DOMAIN,
    )
    registry.register(
        name="note_append",
        description="Add to the end of an existing note.",
        parameters=schema_object(
            {
                "id": {"type": "string", "description": "The note's title or id."},
                "text": {"type": "string", "description": "What to add."},
            },
            required=["id", "text"],
        ),
        handler=tool_append,
        domain=DOMAIN,
    )
    registry.register(
        name="note_search",
        description=(
            "Find notes, or read one whole. Give `id` for a named note's full "
            "text, `query` to search. Call it before saying you do not know "
            "something the user may have written down."
        ),
        parameters=schema_object(
            {
                "query": {"type": "string", "description": "Text to search for."},
                "id": {"type": "string", "description": "A note's title or id, read in full."},
                "tag": {"type": "string", "description": "Restrict to one tag."},
            },
        ),
        handler=tool_search,
        domain=DOMAIN,
    )


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    cfg = config if isinstance(config, dict) else {}
    root = Path(str(cfg.get("path") or DEFAULT_PATH))
    if not root.is_absolute():
        root = Path(jarvis.config_dir) / root
    store = NoteStore(jarvis, root, max_bytes=int(cfg.get("max_bytes") or DEFAULT_MAX_BYTES))
    store.load()
    jarvis.data[DATA_STORE] = store
    _register_services(jarvis, store)
    _register_tools(jarvis, store)
    jarvis.register_shutdown(_closer(store))
    return True


def _closer(store: NoteStore):
    async def close() -> None:
        store.close()

    return close
