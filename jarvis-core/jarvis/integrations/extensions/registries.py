"""Reading the two public registries an operator may name as sources.

`catalog.py` reads `file://` sources and refuses, on purpose, to reach out;
this module is the one place that does, and it reaches out to exactly three
hosts — see [ALLOWED_HOSTS]. An operator who writes

    extensions:
      catalog:
        sources:
          - name: anthropic-skills
            url: https://api.github.com/repos/anthropics/skills/contents/skills
            kind: skill
          - name: mcp-registry
            url: https://registry.modelcontextprotocol.io/v0/servers
            kind: mcp

gets Anthropic's published skills and the MCP registry's http servers in the
console's catalogue, with the same INSTALL flow the bundled skills have: a
plan first (what it is, what it asks for, every file that looks like a
program), then a person, then the write.

What this does NOT do:

* run anything. A skill is fetched as files and read; an MCP server is a URL
  that becomes a `mcp:` entry at the manager's default tier, which is a held
  call until somebody approves the tool. Registry entries that only ship as
  a *package* (npm, pypi, docker — a program this machine would start) are
  skipped and counted, never installed: that is the stdio refusal in
  `catalog.REFUSED_KINDS`, applied to a registry.
* trust the text. Every description goes through [quarantine] via
  `entry_from_raw`, so a listing that says "ignore the permissions above"
  arrives saying exactly that, labelled as a catalogue entry.
* follow `latest`. A GitHub folder is pinned to the branch's commit at the
  moment it was browsed, and the plan hashes the bytes that were fetched; the
  install refuses bytes with a different hash.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .catalog import MAX_ENTRIES, CatalogError, Entry, Source, entry_from_raw
from .manifest import KIND_MCP, KIND_SKILL

_LOGGER = logging.getLogger(__name__)

#: The hosts this module will talk to, and no other. A source URL on any
#: other host is read as a plain JSON index (`{"entries": [...]}`) — the same
#: shape a `file://` catalogue has — and only if its host is here. Widening
#: this list is a code change somebody reads, not a configuration line.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {"api.github.com", "raw.githubusercontent.com", "registry.modelcontextprotocol.io"}
)

READER_GITHUB = "github"
READER_MCP_REGISTRY = "mcp-registry"
READER_INDEX = "index"

#: A GitHub contents URL: owner, repo, path — the only GitHub shape read.
_GITHUB_CONTENTS = re.compile(r"^/repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/contents/(.*)$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")

#: Limits that keep one browse from becoming a crawl, or one skill from
#: becoming a download.
TIMEOUT_S = 12.0
MAX_BODY = 2_000_000
MAX_PAGES = 5
PAGE_SIZE = 100
MAX_SKILL_FILES = 80
MAX_SKILL_BYTES = 2_000_000

#: The registry's remote transports this house can speak. `sse` is a
#: transport the mcp integration does not implement (it has http and stdio),
#: so a server offering only `sse` is skipped and counted, not mis-added.
HTTP_TRANSPORTS = ("streamable-http", "http")


class RegistryError(CatalogError):
    """A registry answered with something that will not be used, and why."""


def reader_for(source: Source) -> str:
    """Which reader a source needs, or "" for a `file://` source."""
    parsed = urlparse(source.url)
    if parsed.scheme == "file":
        return ""
    host = parsed.hostname or ""
    if host not in ALLOWED_HOSTS:
        raise RegistryError(
            f"{host or source.url!r} is not a registry host this house reads; "
            f"it reads {', '.join(sorted(ALLOWED_HOSTS))}"
        )
    if host == "api.github.com" and _GITHUB_CONTENTS.match(parsed.path):
        return READER_GITHUB
    if host == "registry.modelcontextprotocol.io":
        return READER_MCP_REGISTRY
    return READER_INDEX


def _check_host(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") not in ALLOWED_HOSTS:
        raise RegistryError(f"{url!r} is not https on a registry host")
    return url


async def fetch_json(client: httpx.AsyncClient, url: str, *, params: dict[str, Any] | None = None) -> Any:
    """GET one JSON document from an allowed host, capped in size and time."""
    _check_host(url)
    try:
        response = await client.get(
            url,
            params=params,
            headers={"Accept": "application/json", "User-Agent": "jarvis-extensions"},
            timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as err:
        raise RegistryError(f"{url}: {err.__class__.__name__}: {err}") from err
    if response.status_code != 200:
        raise RegistryError(f"{url}: HTTP {response.status_code}")
    if len(response.content) > MAX_BODY:
        raise RegistryError(f"{url}: {len(response.content)} bytes, {MAX_BODY} is the limit")
    try:
        return response.json()
    except ValueError as err:
        raise RegistryError(f"{url}: not JSON ({err})") from err


async def fetch_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    """GET one file from an allowed host. Used for skill files only."""
    _check_host(url)
    try:
        response = await client.get(
            url, headers={"User-Agent": "jarvis-extensions"}, timeout=TIMEOUT_S
        )
    except httpx.HTTPError as err:
        raise RegistryError(f"{url}: {err.__class__.__name__}: {err}") from err
    if response.status_code != 200:
        raise RegistryError(f"{url}: HTTP {response.status_code}")
    if len(response.content) > MAX_BODY:
        raise RegistryError(f"{url}: {len(response.content)} bytes, {MAX_BODY} is the limit")
    return response.content


# --- GitHub: a folder of skill folders -------------------------------------


def github_parts(url: str) -> tuple[str, str, str, str]:
    """(owner, repo, path, branch) from a contents URL; branch from `?ref=`."""
    parsed = urlparse(url)
    match = _GITHUB_CONTENTS.match(parsed.path)
    if parsed.hostname != "api.github.com" or not match:
        raise RegistryError(f"{url!r} is not a GitHub contents URL")
    owner, repo, path = match.group(1), match.group(2), match.group(3).strip("/")
    branch = (parse_qs(parsed.query).get("ref") or ["main"])[0]
    if not re.match(r"^[A-Za-z0-9._/-]{1,100}$", branch):
        raise RegistryError(f"{branch!r} is not a usable branch")
    return owner, repo, path, branch


def parse_github_listing(rows: Any, source: Source, *, owner: str, repo: str, commit: str) -> list[Entry]:
    """Every directory in the listing is one skill, pinned to `commit`.

    Only directories: a skill is a folder with a SKILL.md in it (checked when
    the folder is fetched, by `install.read_payload`), and a loose file at the
    top of the listing is a README, not a skill.
    """
    if not isinstance(rows, list):
        raise RegistryError(f"{source.name}: GitHub did not answer with a listing")
    if not _SHA.match(commit):
        raise RegistryError(f"{source.name}: {commit!r} is not a commit")
    out: list[Entry] = []
    for row in rows[:MAX_ENTRIES]:
        if not isinstance(row, dict) or row.get("type") != "dir":
            continue
        name = str(row.get("name") or "")
        try:
            entry = entry_from_raw(
                {
                    "id": name,
                    "kind": KIND_SKILL,
                    # The folder's contents URL, pinned: `?ref=<commit>` makes
                    # the fetch return the bytes that were browsed, not
                    # whatever the branch says by the time somebody approves.
                    "url": f"https://api.github.com/repos/{owner}/{repo}/contents/{row.get('path') or name}?ref={commit}",
                    "description": f"a skill folder in {owner}/{repo}",
                    "author": owner,
                    "ref": commit[:12],
                },
                source,
            )
        except CatalogError as err:
            _LOGGER.warning("registry %s: skipping %r: %s", source.name, name, err)
            continue
        out.append(entry)
    return out


async def read_github_skills(client: httpx.AsyncClient, source: Source) -> list[Entry]:
    """The skills a GitHub folder offers, pinned to the branch's commit now."""
    owner, repo, path, branch = github_parts(source.url)
    head = await fetch_json(client, f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}")
    commit = str((head or {}).get("sha") or "") if isinstance(head, dict) else ""
    if not _SHA.match(commit):
        raise RegistryError(f"{source.name}: GitHub gave no commit for {branch}")
    rows = await fetch_json(
        client,
        f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
        params={"ref": commit},
    )
    return parse_github_listing(rows, source, owner=owner, repo=repo, commit=commit)


async def fetch_github_skill(client: httpx.AsyncClient, entry: Entry) -> dict[str, bytes]:
    """Every file under a skill folder, as `install.fetch_local` would read it.

    ONE request for the repository's tree at the pinned commit (the git
    trees API, recursive), then one per file from `raw.githubusercontent.com`
    — never from a URL the listing could point anywhere. The contents API a
    folder at a time cost a request per sub-folder and hit the 24-request
    bound on the twentieth house (27 Aug 2026: canvas-design has scripts and
    examples inside). Bounded by files and bytes so a folder that is really
    a repository does not become a download.
    """
    owner, repo, root, ref = github_parts(entry.url)
    if not _SHA.match(ref):
        raise RegistryError(f"{entry.id}: the folder is not pinned to a commit ({ref!r})")
    tree = await fetch_json(
        client, f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}", params={"recursive": "1"}
    )
    rows = tree.get("tree") if isinstance(tree, dict) else None
    if not isinstance(rows, list):
        raise RegistryError(f"{entry.id}: GitHub did not answer with a tree")
    if isinstance(tree, dict) and tree.get("truncated"):
        raise RegistryError(f"{entry.id}: the repository's tree is too large to read in one piece")
    prefix = root.rstrip("/") + "/"
    wanted: list[tuple[str, int]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "")
        if not path.startswith(prefix):
            continue
        relative = path[len(prefix):]
        kind = str(row.get("type") or "")
        if kind == "tree":
            continue
        if kind != "blob" or str(row.get("mode") or "") == "120000":
            # A symlink (mode 120000) or a submodule (commit): the two ways a
            # folder of plain files reaches outside itself. Refused, as
            # fetch_local refuses a symlink.
            raise RegistryError(f"{entry.id}: {relative} is a {'symlink' if kind == 'blob' else kind}, not a file")
        if not all(re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$", part) for part in relative.split("/")):
            raise RegistryError(f"{entry.id}: {relative!r} is not a usable file name")
        wanted.append((relative, int(row.get("size") or 0)))
    if not wanted:
        raise RegistryError(f"{entry.id}: the folder {root} in {owner}/{repo} is empty")
    if len(wanted) > MAX_SKILL_FILES:
        raise RegistryError(f"{entry.id}: {len(wanted)} files; {MAX_SKILL_FILES} is the limit for one skill")
    if sum(size for _, size in wanted) > MAX_SKILL_BYTES:
        raise RegistryError(f"{entry.id}: {sum(size for _, size in wanted)} bytes; {MAX_SKILL_BYTES} is the limit")
    files: dict[str, bytes] = {}
    for relative, _ in wanted:
        files[relative] = await fetch_bytes(
            client, f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{prefix}{relative}"
        )
    return files


# --- The MCP registry: servers with an http remote -------------------------


def mcp_entry_id(name: str) -> str:
    """A registry name (`io.github.owner/server`) as a catalogue id.

    The registry's names carry a `/` the id pattern refuses (an id must never
    be a path); the publisher's reverse-DNS prefix is kept because it is what
    tells `io.github.alice/weather` from `io.github.mallory/weather`.
    """
    lowered = name.strip().lower()
    return re.sub(r"[^a-z0-9._-]+", "-", lowered).strip("-")[:64]


def latest_only(items: list[Any]) -> list[dict[str, Any]]:
    """One row per server name: the one the registry marks latest.

    The registry lists every published VERSION as a row (`ac.inference.sh/mcp`
    came back four times in five rows), and a catalogue that offered four
    INSTALL buttons for one server would be four ways to pick the wrong one.
    Kept: the row whose `_meta…isLatest` is true; failing that (the flag is
    per page and a name can straddle two) the highest version string seen.
    """
    best: dict[str, tuple[int, str, dict[str, Any]]] = {}
    order: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        server = item.get("server") if isinstance(item.get("server"), dict) else item
        name = str(server.get("name") or "")
        if not name:
            continue
        meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
        official = meta.get("io.modelcontextprotocol.registry/official") or {}
        latest = 1 if isinstance(official, dict) and official.get("isLatest") else 0
        version = str(server.get("version") or "")
        rank = (latest, version)
        if name not in best:
            order.append(name)
            best[name] = (latest, version, server)
        elif rank > (best[name][0], best[name][1]):
            best[name] = (latest, version, server)
    return [best[name][2] for name in order]


def parse_mcp_servers(payload: Any, source: Source) -> tuple[list[Entry], int]:
    """(entries with an https remote this house can speak, how many were skipped).

    Skipped means: no remote at all (a package this machine would have to
    start), a remote whose transport the mcp integration does not implement
    (`sse`), or a remote that is not https (a server carries a token).
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
        raise RegistryError(f"{source.name}: the registry did not answer with servers")
    out: list[Entry] = []
    skipped = 0
    for server in latest_only(payload["servers"][:MAX_ENTRIES]):
        name = str(server.get("name") or "")
        remote = ""
        for candidate in server.get("remotes") or []:
            if not isinstance(candidate, dict):
                continue
            url = str(candidate.get("url") or "")
            if str(candidate.get("type") or "") in HTTP_TRANSPORTS and url.startswith("https://"):
                remote = url
                break
        if not name or not remote:
            skipped += 1
            continue
        try:
            entry = entry_from_raw(
                {
                    "id": mcp_entry_id(name),
                    "kind": KIND_MCP,
                    "url": remote,
                    "version": server.get("version"),
                    "description": server.get("description") or server.get("title"),
                    # The publisher is the part before the slash: reverse-DNS
                    # of who the registry verified, which is the one thing in
                    # the listing that is not the server's own claim.
                    "author": name.split("/", 1)[0],
                    "ref": str(server.get("version") or "").strip() or "unversioned",
                },
                source,
            )
        except CatalogError as err:
            _LOGGER.warning("registry %s: skipping %r: %s", source.name, name, err)
            skipped += 1
            continue
        out.append(entry)
    return out, skipped


async def read_mcp_registry(
    client: httpx.AsyncClient, source: Source, query: str = ""
) -> tuple[list[Entry], int]:
    """Up to MAX_PAGES pages of the registry, searched server-side when asked."""
    entries: list[Entry] = []
    skipped = 0
    cursor = ""
    for _ in range(MAX_PAGES):
        # `version=latest` asks the registry for one row per server; the page
        # is deduped again on this side because the parameter is the
        # registry's promise, and `latest_only` is this house's.
        params: dict[str, Any] = {"limit": PAGE_SIZE, "version": "latest"}
        if query:
            params["search"] = query
        if cursor:
            params["cursor"] = cursor
        payload = await fetch_json(client, source.url, params=params)
        page, missed = parse_mcp_servers(payload, source)
        entries.extend(page)
        skipped += missed
        meta = payload.get("metadata") if isinstance(payload, dict) else None
        cursor = str((meta or {}).get("nextCursor") or "") if isinstance(meta, dict) else ""
        if not cursor or len(entries) >= MAX_ENTRIES:
            break
    return entries[:MAX_ENTRIES], skipped


# --- A plain index on an allowed host ----------------------------------------


def parse_index(payload: Any, source: Source) -> list[Entry]:
    """The `file://` index shape over https: `{"entries": [...]}`, absolute URLs only."""
    rows = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise RegistryError(f"{source.name}: no entries")
    out: list[Entry] = []
    for row in rows[:MAX_ENTRIES]:
        try:
            entry = entry_from_raw(row, source)
            _check_host(entry.url)
        except CatalogError as err:
            _LOGGER.warning("registry %s: skipping an entry: %s", source.name, err)
            continue
        out.append(entry)
    return out


# --- One door ---------------------------------------------------------------


async def read_remote(
    client: httpx.AsyncClient, source: Source, query: str = ""
) -> tuple[list[Entry], int]:
    """Everything a remote source offers that this house can install.

    Returns (entries, skipped): the count is surfaced beside the listing so
    "the registry has 900 servers and the catalogue shows 40" is a sentence
    the console can say instead of a question an operator has to ask.
    """
    reader = reader_for(source)
    if reader == READER_GITHUB:
        entries = await read_github_skills(client, source)
        skipped = 0
    elif reader == READER_MCP_REGISTRY:
        entries, skipped = await read_mcp_registry(client, source, query)
    elif reader == READER_INDEX:
        entries = parse_index(await fetch_json(client, source.url), source)
        skipped = 0
    else:
        raise RegistryError(f"{source.name} is on this machine; read it with the catalogue")
    needle = str(query or "").strip().lower()
    if needle:
        entries = [e for e in entries if needle in f"{e.id} {e.description} {e.author}".lower()]
    return entries, skipped


async def fetch_remote(client: httpx.AsyncClient, entry: Entry) -> dict[str, bytes]:
    """The files of one remote skill. An MCP entry has no files to fetch."""
    if entry.kind != KIND_SKILL:
        raise RegistryError(f"{entry.id} is a {entry.kind}; there is nothing to download")
    if urlparse(entry.url).hostname == "api.github.com":
        return await fetch_github_skill(client, entry)
    raise RegistryError(
        f"{entry.id}: {entry.url} is not a folder this house knows how to read; "
        "a remote skill comes from a GitHub folder"
    )


__all__ = [
    "ALLOWED_HOSTS",
    "HTTP_TRANSPORTS",
    "RegistryError",
    "fetch_github_skill",
    "fetch_json",
    "fetch_remote",
    "github_parts",
    "mcp_entry_id",
    "parse_github_listing",
    "parse_index",
    "parse_mcp_servers",
    "read_github_skills",
    "read_mcp_registry",
    "read_remote",
    "reader_for",
]
