"""Every `.env` variable, set from the console and kept (M114).

The operator's report of 27 Aug 2026: "allow setting all .env variables in
the jarvis console settings, and have them persist". `.env` is read by
compose when the container starts and by `!env_var` in configuration.yaml;
the house could neither show it nor change it.

Two halves, kept apart on purpose:

* the **catalogue** — every variable `.env.example` names, with the comment
  above it as the why and whether it is a secret. It ships with the core
  (`/srv/.env.example` in the image, the repository's file on a bare host),
  so the list the console shows is the list the documentation keeps, and
  `test_packaging` holds the two together;
* the **overrides** — what the console set, in
  `<config>/.storage/environment.json`, applied over the process environment
  at boot BEFORE configuration is read, so `!env_var` and every integration
  see them. An override wins over the container's environment; clearing it
  puts the environment's own value back at the next boot.

What this does NOT do: write `.env` on the host (the file is precious, the
container cannot see it, and a house that rewrote it would be a house that
rewrote the operator's secrets); or apply a change live — the value a running
integration read at setup is the value it has, and the row says "applies on
restart" until the next boot.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: The catalogue, as shipped: the image copies `.env.example` beside the
#: package; a bare host has the repository's own file one directory up.
CATALOG_CANDIDATES = (
    Path(__file__).resolve().parent.parent / ".env.example",
    Path("/srv/.env.example"),
)

STORE_KEY = "environment"
_NAME = re.compile(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)=(.*)$")
_SECRET = re.compile(r"(TOKEN|SECRET|KEY|PASSWORD|PASSWD|PASS)(_|$)")
MAX_VALUE = 4096
MASK = "••••••••"


@dataclass
class Variable:
    """One line of `.env.example`: the name, its why, its example value."""

    name: str
    why: str = ""
    default: str = ""
    #: Masked in every listing; shown only through `reveal`, which is audited.
    secret: bool = False
    #: The section heading the variable sits under, when the file has one.
    section: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "why": self.why,
            "default": self.default,
            "secret": self.secret,
            "section": self.section,
        }


def is_secret(name: str) -> bool:
    return bool(_SECRET.search(name.upper()))


def parse_catalog(text: str) -> list[Variable]:
    """Every `NAME=` line, with the comment block right above it as the why.

    A blank line ends a comment block; a `# ---` rule or an all-caps comment
    line names a section. A commented-out assignment (`# NAME=value`) is a
    documented variable too — the file documents several that way — and is
    listed with its example value.
    """
    out: list[Variable] = []
    seen: set[str] = set()
    comment: list[str] = []
    section = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            comment = []
            continue
        if stripped.startswith("#"):
            body = stripped.lstrip("#").strip()
            if re.match(r"^-{3,}$|^={3,}$", body):
                continue
            hidden = _NAME.match(body)
            if hidden and hidden.group(1) not in seen:
                # `# NAME=value`: a variable the file documents but leaves unset.
                out.append(Variable(hidden.group(1), " ".join(comment).strip(), hidden.group(2).strip().strip('"'), is_secret(hidden.group(1)), section))
                seen.add(hidden.group(1))
                comment = []
                continue
            if body and body.upper() == body and len(body) < 60 and not comment:
                section = body.title()
                continue
            comment.append(body)
            continue
        match = _NAME.match(stripped)
        if match:
            name, value = match.group(1), match.group(2).strip()
            if name in seen:
                comment = []
                continue
            out.append(Variable(name, " ".join(comment).strip(), value.strip('"'), is_secret(name), section))
            seen.add(name)
        comment = []
    return out


def load_catalog() -> list[Variable]:
    for path in CATALOG_CANDIDATES:
        try:
            return parse_catalog(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    _LOGGER.warning("environment: no .env.example to read the catalogue from")
    return []


# --- the overrides -----------------------------------------------------------


def store_path(config_dir: str | Path) -> Path:
    return Path(config_dir) / ".storage" / f"{STORE_KEY}.json"


def read_overrides(config_dir: str | Path) -> dict[str, str]:
    """The overrides as kept, or nothing. Plain JSON: this runs before the
    house exists, in the entrypoint, with no loop and no Store."""
    path = store_path(config_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        _LOGGER.exception("environment: %s is unreadable; ignoring the overrides", path)
        return {}
    data = payload.get("data") if isinstance(payload, dict) else None
    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(values, dict):
        return {}
    return {str(k): str(v) for k, v in values.items() if _NAME.match(f"{k}=")}


def write_overrides(config_dir: str | Path, values: dict[str, str]) -> None:
    path = store_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": 1, "data": {"values": dict(values)}}, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def apply_overrides(config_dir: str | Path, environ: dict[str, str] | None = None) -> list[str]:
    """Put the kept overrides into the environment. Returns the names applied.

    Called first thing in the entrypoint, before configuration.yaml is read.
    An override wins over what the container was started with — that is what
    "set from the console" means — and the original is remembered under
    `_JARVIS_ENV_ORIGINAL_<name>` so the console can say which value the
    environment itself carries.
    """
    env = os.environ if environ is None else environ
    applied: list[str] = []
    for name, value in read_overrides(config_dir).items():
        if name in env and f"_JARVIS_ENV_ORIGINAL_{name}" not in env:
            env[f"_JARVIS_ENV_ORIGINAL_{name}"] = env[name]
        env[name] = value
        applied.append(name)
    if applied:
        _LOGGER.info("environment: %d override(s) applied from the console: %s", len(applied), ", ".join(sorted(applied)))
    return applied


@dataclass
class Environment:
    """What the console sees and changes. One per house."""

    config_dir: Path
    catalog: list[Variable] = field(default_factory=list)
    overrides: dict[str, str] = field(default_factory=dict)
    #: The value each override had when this process started, so a change
    #: made since boot can be told from one already live.
    booted_with: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, config_dir: str | Path) -> "Environment":
        env = cls(Path(config_dir), load_catalog(), read_overrides(config_dir))
        env.booted_with = dict(env.overrides)
        return env

    def variable(self, name: str) -> Variable | None:
        return next((v for v in self.catalog if v.name == name), None)

    def rows(self, environ: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """One row per catalogued variable — never a secret's value."""
        env = os.environ if environ is None else environ
        out: list[dict[str, Any]] = []
        for var in self.catalog:
            override = self.overrides.get(var.name)
            live = env.get(var.name)
            original = env.get(f"_JARVIS_ENV_ORIGINAL_{var.name}")
            if var.name in self.booted_with:
                source = "override"
                environment_value = original
            elif live is not None:
                source = "environment"
                environment_value = live
            else:
                source = "unset"
                environment_value = None
            pending = (override or "") != (self.booted_with.get(var.name) or "")
            row = {
                **var.as_dict(),
                "set": override is not None,
                "source": source,
                "is_set_in_environment": environment_value is not None,
                "pending": pending,
                "value": (MASK if var.secret and override else override) if override is not None else None,
                "live": (MASK if var.secret and live else live) if live is not None else None,
            }
            out.append(row)
        return out

    def set(self, name: str, value: Any) -> dict[str, Any]:
        var = self.variable(name)
        if var is None:
            return {"status": "error", "error": f"{name!r} is not a variable .env.example names"}
        text = str(value if value is not None else "")
        if len(text) > MAX_VALUE:
            return {"status": "error", "error": f"{name}: {len(text)} characters; {MAX_VALUE} is the limit"}
        if "\n" in text or "\r" in text:
            return {"status": "error", "error": f"{name}: one line, please"}
        self.overrides[name] = text
        write_overrides(self.config_dir, self.overrides)
        _LOGGER.info("environment: %s set from the console%s (applies on restart)", name, "" if var.secret else f" to {text!r}")
        return {"status": "ok", "name": name, "pending": True}

    def clear(self, name: str) -> dict[str, Any]:
        if name not in self.overrides:
            return {"status": "error", "error": f"{name} is not set from the console"}
        self.overrides.pop(name, None)
        write_overrides(self.config_dir, self.overrides)
        _LOGGER.info("environment: %s cleared from the console (the environment's own value applies on restart)", name)
        return {"status": "ok", "name": name, "pending": True}

    def reveal(self, name: str) -> dict[str, Any]:
        var = self.variable(name)
        if var is None:
            return {"status": "error", "error": f"{name!r} is not a variable .env.example names"}
        value = self.overrides.get(name)
        if value is None:
            value = os.environ.get(name)
        _LOGGER.info("environment: %s revealed on the console", name)
        return {"status": "ok", "name": name, "value": value}


__all__ = [
    "Environment",
    "MASK",
    "Variable",
    "apply_overrides",
    "is_secret",
    "load_catalog",
    "parse_catalog",
    "read_overrides",
    "write_overrides",
]
