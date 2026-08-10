"""Tools created from the console, stored beside the ones in YAML.

Same reasoning as `automation/authored.py`: a `*.tool.yaml` manifest is the
user's file, and a round-trip writer would reformat it and lose their
comments. So console-created tools live in `.storage/tools.json` and are added
to the same `specs` list the YAML manifests feed, which means
`build_yaml_tools` builds them and nothing downstream can tell the difference.

Unlike an automation, a tool's identity is its **name**, because that is the
word the model says to call it. Two consequences shape this module.

**A name is claimed, not minted.** There is no `ui_` prefix to hide behind: a
tool called `unlock_the_door` has to be called that for the model to use it.
So `validate` refuses a name that a built-in or a YAML tool already holds,
rather than letting a console tool quietly shadow one. Shadowing `lock_control`
would be a way to make the assistant call something else entirely while every
log line still said `lock_control`.

**Deleting is by name too**, and only names this store owns can be deleted, so
the API cannot be used to remove a built-in or a tool from the user's YAML.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ..store import Store

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "tools"
STORAGE_VERSION = 1
DATA_AUTHORED_TOOLS = "authored_tools"

#: What the model is allowed to be asked to say. Deliberately narrow: the name
#: goes into a JSON schema the model is shown, and into every log line.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,47}$")

ALLOWED_FIELDS = frozenset({"name", "description", "tier", "domain", "service"})
ALLOWED_SERVICE_FIELDS = frozenset(
    {"method", "url", "headers", "fields", "payload", "json", "body", "timeout"}
)
ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})

#: Fields the store keeps for itself, stripped before re-validating.
BOOKKEEPING = ("created_at", "updated_at")

MAX_DESCRIPTION = 400
MAX_FIELDS = 20


class AuthoredToolError(Exception):
    """A proposed tool was refused. The message is shown to the user."""


def validate(spec: Any, taken: set[str] | None = None) -> dict[str, Any]:
    """Check and normalise one tool manifest. Raises [AuthoredToolError].

    `taken` is the set of names already registered by something that is not
    this store — built-ins and the user's YAML. See the module docstring for
    why shadowing one is refused rather than allowed to win.
    """
    if not isinstance(spec, dict):
        raise AuthoredToolError("A tool must be an object.")

    unknown = set(spec) - ALLOWED_FIELDS
    if unknown:
        raise AuthoredToolError(f"Unknown field(s): {', '.join(sorted(unknown))}.")

    name = str(spec.get("name") or "").strip().lower()
    if not name:
        raise AuthoredToolError("Give it a name.")
    if not NAME_RE.match(name):
        raise AuthoredToolError(
            "The name must be 3-48 characters, lowercase letters, digits and "
            "underscores, starting with a letter."
        )
    if taken and name in taken:
        raise AuthoredToolError(
            f"{name} is already a tool. Pick another name — a second tool with "
            "this name would shadow the first, and the logs could not tell them apart."
        )

    description = str(spec.get("description") or "").strip()
    if not description:
        # Without one the model has nothing to decide from and will either
        # never call the tool or call it for the wrong thing.
        raise AuthoredToolError("Describe what it does, or the model cannot use it.")
    if len(description) > MAX_DESCRIPTION:
        raise AuthoredToolError(f"The description must be under {MAX_DESCRIPTION} characters.")

    tier = spec.get("tier", 1)
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        raise AuthoredToolError("Tier must be 1, 2 or 3.") from None
    if tier not in (1, 2, 3):
        raise AuthoredToolError("Tier must be 1, 2 or 3.")

    service = spec.get("service")
    if not isinstance(service, dict):
        raise AuthoredToolError("A tool needs a `service` block saying what it calls.")
    unknown = set(service) - ALLOWED_SERVICE_FIELDS
    if unknown:
        raise AuthoredToolError(f"Unknown service field(s): {', '.join(sorted(unknown))}.")

    url = str(service.get("url") or "").strip()
    if not url:
        raise AuthoredToolError("The service needs a `url`.")
    # Checked on the literal prefix rather than after rendering: a template can
    # only appear later in the string, and a `url` that starts with `{{` has no
    # scheme anyone can reason about.
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise AuthoredToolError("The url must start with http:// or https://.")

    method = str(service.get("method") or "GET").strip().upper()
    if method not in ALLOWED_METHODS:
        raise AuthoredToolError(f"Method must be one of {', '.join(sorted(ALLOWED_METHODS))}.")

    fields = service.get("fields") or {}
    if not isinstance(fields, dict):
        raise AuthoredToolError("`fields` must be an object keyed by field name.")
    if len(fields) > MAX_FIELDS:
        raise AuthoredToolError(f"That is more than {MAX_FIELDS} fields.")
    for key, value in fields.items():
        if not NAME_RE.match(str(key).strip().lower()):
            raise AuthoredToolError(f"{key!r} is not a usable field name.")
        if value is not None and not isinstance(value, dict):
            raise AuthoredToolError(f"Field {key!r} must be an object.")

    headers = service.get("headers") or {}
    if not isinstance(headers, dict):
        raise AuthoredToolError("`headers` must be an object.")
    for key, value in headers.items():
        # A newline in a header value is header injection; the renderer writes
        # these straight onto the wire.
        if any(ch in str(value) for ch in "\r\n") or any(ch in str(key) for ch in "\r\n"):
            raise AuthoredToolError("Headers cannot contain line breaks.")

    timeout = service.get("timeout")
    if timeout is not None:
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            raise AuthoredToolError("`timeout` must be a number of seconds.") from None
        if not 1 <= timeout <= 300:
            raise AuthoredToolError("`timeout` must be between 1 and 300 seconds.")

    clean_service: dict[str, Any] = {"url": url, "method": method}
    if fields:
        clean_service["fields"] = fields
    if headers:
        clean_service["headers"] = headers
    if timeout is not None:
        clean_service["timeout"] = timeout
    for key in ("payload", "json", "body"):
        if service.get(key) is not None:
            clean_service[key] = service[key]

    clean: dict[str, Any] = {
        "name": name,
        "description": description,
        "tier": tier,
        "service": clean_service,
    }
    if spec.get("domain"):
        # `const.GATED_DOMAINS` holds the line regardless of the declared tier,
        # so this can only ever tighten the gate, never loosen it.
        clean["domain"] = str(spec["domain"]).strip().lower()
    return clean


class AuthoredToolStore:
    """The console-created tools, persisted."""

    def __init__(self, config_dir: Any) -> None:
        self._store = Store(config_dir, STORAGE_KEY, STORAGE_VERSION)
        self.items: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> list[dict[str, Any]]:
        """Read the store, dropping anything that no longer validates."""
        stored = await self._store.load() or {}
        raw = stored.get("items") if isinstance(stored, dict) else None
        items: dict[str, dict[str, Any]] = {}
        for entry in (raw or []):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            try:
                clean = validate({k: v for k, v in entry.items() if k not in BOOKKEEPING})
            except AuthoredToolError as err:
                _LOGGER.warning("tools: dropping %s — %s", name or "<unnamed>", err)
                continue
            clean["created_at"] = entry.get("created_at")
            clean["updated_at"] = entry.get("updated_at")
            items[clean["name"]] = clean
        self.items = items
        return self.specs()

    def specs(self) -> list[dict[str, Any]]:
        """What `build_yaml_tools` wants: manifests, without the bookkeeping."""
        return [
            {k: v for k, v in item.items() if k not in ("created_at", "updated_at")}
            for item in self.items.values()
        ]

    def entries(self) -> list[dict[str, Any]]:
        return list(self.items.values())

    async def _async_save(self) -> None:
        await self._store.save({"items": list(self.items.values())})

    async def async_create(self, spec: Any, taken: set[str] | None = None) -> dict[str, Any]:
        clean = validate(spec, taken)
        now = time.time()
        clean["created_at"] = now
        clean["updated_at"] = now
        self.items[clean["name"]] = clean
        await self._async_save()
        return clean

    async def async_update(
        self, name: str, spec: Any, taken: set[str] | None = None
    ) -> dict[str, Any]:
        existing = self.items.get(name)
        if existing is None:
            raise AuthoredToolError(f"{name} is not a tool this console created.")
        # Its own name is not a collision with itself.
        clean = validate(spec, (taken or set()) - {name})
        if clean["name"] != name:
            raise AuthoredToolError(
                "A tool's name cannot be changed — the model calls it by that "
                "word. Delete it and create the replacement."
            )
        clean["created_at"] = existing.get("created_at")
        clean["updated_at"] = time.time()
        self.items[name] = clean
        await self._async_save()
        return clean

    async def async_delete(self, name: str) -> bool:
        if name not in self.items:
            return False
        del self.items[name]
        await self._async_save()
        return True



def get_authored_tools(jarvis: "Jarvis") -> AuthoredToolStore:
    """The shared store, created on first use."""
    store = jarvis.data.get(DATA_AUTHORED_TOOLS)
    if not isinstance(store, AuthoredToolStore):
        store = AuthoredToolStore(jarvis.config_dir)
        jarvis.data[DATA_AUTHORED_TOOLS] = store
    return store
