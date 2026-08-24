"""``save_note`` / ``find_note`` — the hub's notes, from this machine.

The desktop agent already reads the clipboard, the screen and the filesystem.
What it could not do was *keep* anything: a snippet worth remembering had to be
dictated back to Jarvis, or pasted into some other application, and the whole
point of the agent is that the two machines are one system.

So these two reach the hub's notes API — the same `<config>/notes/*.md` files
the console edits and the model writes research into. They are the only actions
that talk to jarvis-core rather than to this desktop, and that is deliberate:
notes belong to the house, not to the laptop, and a note taken here should be
readable from the phone.

Tiers: writing a note is Tier 2 (it costs nothing and is visible); reading is
Tier 2 as well, because it returns text from the user's own store rather than
from this machine. Neither touches the local filesystem, so `PathScope` has no
part in either.
"""

from __future__ import annotations

import json as jsonlib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..policy import ActionTier
from .base import Action, ActionContext, ActionResult

__all__ = ["SaveNote", "FindNote", "hub_base_url"]

DEFAULT_TIMEOUT = 20.0
MAX_BODY_BYTES = 128 * 1024


def hub_base_url(ctx: ActionContext) -> str:
    """The hub's http(s) base, worked out from the websocket URL it dials.

    The agent is configured with one address — `ws://host:8080/api/websocket` —
    and adding a second setting for "the same server, over http" is a second
    thing to get wrong. So it is derived: scheme swapped, path dropped.
    """
    raw = str(getattr(ctx.config, "server_url", "") or "")
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme or "http")
    if not parsed.netloc:
        return ""
    return f"{scheme}://{parsed.netloc}"


class _NoteAction(Action):
    """Shared plumbing: one authenticated request to the hub, or a clear no."""

    capability = "notes"
    tier = ActionTier.NOTIFY
    timeout_s = 30.0

    def available(self, ctx: ActionContext) -> bool:
        return bool(hub_base_url(ctx) and getattr(ctx.config, "token", ""))

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        if not hub_base_url(ctx):
            return "this agent has no server address configured"
        if not getattr(ctx.config, "token", ""):
            return "this agent has no token, so it cannot reach the notes API"
        return None

    def _request(
        self, ctx: ActionContext, method: str, path: str, payload: dict | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        base = hub_base_url(ctx)
        url = f"{base}{path}"
        data = jsonlib.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {ctx.config.token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
                body = response.read(MAX_BODY_BYTES)
        except urllib.error.HTTPError as err:
            # The hub's own message, not a generic failure: "no note 'x'" is
            # the answer, and hiding it behind "request failed" would make the
            # agent the least useful thing in the chain.
            detail = ""
            try:
                detail = jsonlib.loads(err.read(4096) or b"{}").get("message", "")
            except Exception:  # noqa: BLE001 - a non-JSON error body is fine
                detail = ""
            return None, f"the hub refused: {err.code} {detail or err.reason}"
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            return None, f"could not reach the hub: {err}"
        try:
            return jsonlib.loads(body or b"{}"), ""
        except ValueError:
            return None, "the hub answered with something that is not JSON"


class SaveNote(_NoteAction):
    id = "save_note"
    description = "Write a note on the Jarvis hub (markdown, kept with the rest)."
    params_schema = {
        "title": "string: a short title. Writing the same title again is refused.",
        "body": "string (optional): the note, in markdown.",
        "tags": "array (optional): tags to file it under.",
    }

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        title = self.str_param(params, "title")
        if not title:
            return ActionResult.failed("title is required")
        payload: dict[str, Any] = {"title": title, "body": self.str_param(params, "body") or ""}
        tags = params.get("tags")
        if isinstance(tags, (list, tuple)):
            payload["tags"] = [str(tag) for tag in tags][:12]
        answer, error = self._request(ctx, "POST", "/api/notes", payload)
        if answer is None:
            return ActionResult.failed(error)
        note = answer.get("note") or {}
        return ActionResult(ok=True, data={"id": note.get("id"), "title": note.get("title")})


class FindNote(_NoteAction):
    id = "find_note"
    description = "Search the notes on the Jarvis hub, or read one by name."
    params_schema = {
        "query": "string (optional): what to search for.",
        "id": "string (optional): read this note in full instead of searching.",
        "tag": "string (optional): restrict a search to one tag.",
    }

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        note_id = self.str_param(params, "id")
        if note_id:
            answer, error = self._request(
                ctx, "GET", f"/api/notes/{urllib.parse.quote(note_id)}"
            )
            if answer is None:
                return ActionResult.failed(error)
            return ActionResult(ok=True, data={"note": answer.get("note")})

        query = urllib.parse.urlencode(
            {
                key: value
                for key, value in (
                    ("q", self.str_param(params, "query")),
                    ("tag", self.str_param(params, "tag")),
                )
                if value
            }
        )
        answer, error = self._request(ctx, "GET", f"/api/notes?{query}" if query else "/api/notes")
        if answer is None:
            return ActionResult.failed(error)
        rows = answer.get("notes") or []
        return ActionResult(
            ok=True,
            data={
                "count": len(rows),
                "notes": [
                    {"id": row.get("id"), "title": row.get("title"), "tags": row.get("tags")}
                    for row in rows[:20]
                ],
            },
        )
