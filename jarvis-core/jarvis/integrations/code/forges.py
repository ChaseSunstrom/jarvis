"""GitHub and GitLab, and the list of repositories Jarvis may touch.

## The permission model, in one paragraph

A forge is a host plus a credential plus an **allow-list**. Jarvis may clone
and push only repositories on that list; everything else on the account is
invisible to it, including private repositories it has a perfectly good token
for. Repositories Jarvis creates in its own workspace need no entry — it made
them — but pushing one anywhere still needs a forge and a permitted path.

    code:
      forges:
        - name: github
          kind: github
          token: !env_var GITHUB_TOKEN ""
          allow:
            - chasesunstrom/jarvis
            - chasesunstrom/notes

The allow-list is the whole point and it is deliberately not clever. Exact
`owner/name`, or `owner/*` for a whole account. No regular expressions: a
pattern language is a place to make a mistake that reads as correct, and the
mistake here is "the model can now reach a repository you did not mean".

## Why the token never reaches the model or the container

Two separate leaks to close.

The **model** never sees it: no tool returns it, `as_dict()` omits it, and the
console is sent `has_token` rather than the value. A model that could read the
token could use it anywhere, allow-list or not — the list constrains what
Jarvis does, not what the credential can do.

The **container** never sees it either: clone and push happen on the HOST, and
`sandbox.container_argv` passes only the environment's declared `env:`. A job
that could read the token out of its own environment would be one `curl` away
from exfiltrating it, and `network: egress` is a setting people will use.

It is also kept out of the **argv**, because `/proc/*/cmdline` is world
readable and `ps` is how everybody's credentials leak. git gets it through
`GIT_ASKPASS` — a one-line script pointed at by an environment variable, which
is git's own supported way of not putting a password on a command line.

## Push is the outward-facing one

Cloning a permitted repository is reading something Jarvis was told it may
read. Pushing sends work to a server other people can see, and cannot be
undone by deleting a local file — so it is Tier 3, approval-gated, and it
refuses to touch a branch that is not one Jarvis made.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "Forge",
    "ForgeError",
    "askpass_script",
    "clone_url",
    "forge_from_dict",
    "permits",
    "split_project",
]

KIND_GITHUB = "github"
KIND_GITLAB = "gitlab"
KINDS = (KIND_GITHUB, KIND_GITLAB)

DEFAULT_HOSTS = {KIND_GITHUB: "github.com", KIND_GITLAB: "gitlab.com"}
#: The username half of an HTTPS credential. Both forges ignore it and read the
#: password, but they want *something*, and these are the documented ones.
USERNAMES = {KIND_GITHUB: "x-access-token", KIND_GITLAB: "oauth2"}

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
#: `owner/name`, or a deeper GitLab path like `group/subgroup/name`. Each
#: segment is conservative: this becomes a URL and a directory name.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
MAX_ALLOW = 200


class ForgeError(RuntimeError):
    """A forge operation that was refused, with the reason."""


@dataclass
class Forge:
    name: str
    kind: str = KIND_GITHUB
    host: str = ""
    token: str = ""
    #: Exact `owner/name`, or `owner/*`. Empty means nothing is permitted —
    #: NOT everything. A forge with no list is a forge that does nothing, which
    #: is the safe reading of a half-finished configuration.
    allow: list[str] = field(default_factory=list)
    #: Whether a job may push back. Off by default: cloning is reading,
    #: pushing is publishing.
    push: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "host": self.host,
            # Never the token. `has_token` is what a console needs to say
            # "configured" or "set GITHUB_TOKEN"; the value is of no use to it.
            "has_token": bool(self.token),
            "allow": list(self.allow),
            "push": self.push,
        }

    def describe(self) -> str:
        count = len(self.allow)
        return (
            f"{self.kind} at {self.host} · {count} "
            f"repositor{'y' if count == 1 else 'ies'} permitted"
            + ("" if self.push else " · read-only")
        )


def forge_from_dict(raw: Any) -> Forge | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip().lower()
    if not _NAME_RE.match(name):
        _LOGGER.warning("code: %r is not a usable forge name", raw.get("name"))
        return None

    kind = str(raw.get("kind") or KIND_GITHUB).strip().lower()
    if kind not in KINDS:
        _LOGGER.warning(
            "code: forge %s has kind %r; expected one of %s", name, kind, ", ".join(KINDS)
        )
        return None

    host = str(raw.get("host") or DEFAULT_HOSTS[kind]).strip().lower()
    # A host with a scheme, a slash or credentials in it is a mistake that
    # would end up concatenated into a URL.
    if not re.match(r"^[a-z0-9][a-z0-9.-]*(:\d+)?$", host):
        _LOGGER.warning("code: forge %s has an unusable host %r", name, host)
        return None

    allow: list[str] = []
    for entry in raw.get("allow") or []:
        text = str(entry or "").strip()
        if not text:
            continue
        if _valid_pattern(text):
            allow.append(text)
        else:
            _LOGGER.warning(
                "code: forge %s: dropping %r from allow — expected owner/name "
                "or owner/*",
                name,
                text,
            )

    return Forge(
        name=name,
        kind=kind,
        host=host,
        token=str(raw.get("token") or "").strip(),
        allow=allow[:MAX_ALLOW],
        push=bool(raw.get("push")),
    )


def _valid_pattern(text: str) -> bool:
    parts = text.split("/")
    if len(parts) < 2 or len(parts) > 8:
        return False
    for index, part in enumerate(parts):
        if part == "*" and index == len(parts) - 1:
            continue  # only the LAST segment may be a wildcard
        if not _SEGMENT_RE.match(part):
            return False
    return True


def split_project(project: str) -> list[str]:
    """`owner/name` into its segments, or [] if it is not one.

    Refuses `..`, absolute paths, schemes, and anything with a character that
    does not belong in a URL path. This is the string a model supplies, so it
    is checked before it is anywhere near a URL or a directory name.
    """
    text = str(project or "").strip()
    # A leading slash is REFUSED, not stripped. `/etc/passwd` stripped to
    # `etc/passwd` is a perfectly well-formed project path, and answering a
    # confused caller with a valid-looking answer is how a mistake becomes a
    # request somebody has to reason about later.
    if not text or text.startswith("/") or text.endswith("/"):
        return []
    if ".." in text or ":" in text or "\\" in text:
        return []
    parts = text.split("/")
    if len(parts) < 2 or len(parts) > 8:
        return []
    if not all(_SEGMENT_RE.match(part) for part in parts):
        return []
    return parts


def permits(forge: Forge, project: str) -> bool:
    """Whether this forge's allow-list covers `project`.

    Case-insensitive, because both forges treat repository paths that way and
    an allow-list that missed `Owner/Repo` for `owner/repo` would be a rule
    that looks enforced and is not.
    """
    parts = split_project(project)
    if not parts:
        return False
    wanted = [p.lower() for p in parts]
    for pattern in forge.allow:
        segments = [p.lower() for p in pattern.split("/")]
        if segments and segments[-1] == "*":
            head = segments[:-1]
            if wanted[: len(head)] == head and len(wanted) > len(head):
                return True
        elif segments == wanted:
            return True
    return False


def clone_url(forge: Forge, project: str) -> str:
    """The HTTPS URL, with NO credentials in it.

    The token goes through `GIT_ASKPASS` instead: a URL ends up in
    `.git/config`, in `git remote -v`, and in any error message git prints, and
    a token in any of those is a token in a place nobody remembers to clean.
    """
    parts = split_project(project)
    if not parts:
        raise ForgeError(f"{project!r} is not a repository path like owner/name")
    path = "/".join(quote(part, safe="") for part in parts)
    return f"https://{forge.host}/{path}.git"


def redact(text: str, forge: Forge) -> str:
    """Take the token out of anything on its way to a log or a model."""
    if forge.token and forge.token in text:
        return text.replace(forge.token, "<token>")
    return text


def askpass_script(config_dir: Path) -> Path:
    """A one-line helper git calls when it wants a password.

    git's own supported way of supplying a credential without putting it on a
    command line. The script echoes an environment variable; the variable is
    set only on the git subprocess. Neither ever reaches the model, the
    container, or `ps`.

    Mode 0700 and inside the config directory, which is already the place this
    process keeps things nobody else should read.
    """
    path = Path(config_dir) / ".storage" / "git-askpass.sh"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    body = '#!/bin/sh\nprintf %s "$JARVIS_GIT_TOKEN"\n'
    if not path.exists() or path.read_text(encoding="utf-8") != body:
        path.write_text(body, encoding="utf-8")
    path.chmod(stat.S_IRWXU)  # 0700
    return path


def git_env(forge: Forge, config_dir: Path) -> dict[str, str]:
    """The environment a host git needs to authenticate to this forge.

    `GIT_TERMINAL_PROMPT=0` matters as much as the credential: without it a
    git that decides to ask for a username blocks on a terminal nobody is
    sitting at, and the job hangs until its own clock kills it.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Cleared first, then set only if we have something to set. The process
    # environment is inherited so that PATH and friends work, and an ambient
    # `GIT_ASKPASS` — from the operator's own shell, from a CI runner — would
    # otherwise decide how git authenticates on a path where we deliberately
    # supply nothing.
    for key in ("GIT_ASKPASS", "SSH_ASKPASS", "JARVIS_GIT_TOKEN", "GIT_USERNAME"):
        env.pop(key, None)
    if forge.token:
        env["GIT_ASKPASS"] = str(askpass_script(config_dir))
        env["JARVIS_GIT_TOKEN"] = forge.token
        env["GIT_USERNAME"] = USERNAMES.get(forge.kind, "git")
    return env


def local_name(project: str) -> str:
    """What a cloned repository is called locally: the last path segment."""
    parts = split_project(project)
    return parts[-1].lower() if parts else ""


def is_jarvis_branch(branch: str) -> bool:
    """Whether this is a branch Jarvis made, which is all it may push.

    Pushing `main` would put a model's work on the branch other people build
    from, with no review step anywhere. Every branch a job makes starts
    `jarvis/`, so this is exactly "its own work".
    """
    return str(branch or "").startswith("jarvis/")


def check_remote_url(url: str, forge: Forge) -> str:
    """Refuse a remote that is not this forge, or that carries a credential.

    A repository cloned from a permitted path can still have had its `origin`
    rewritten — by a previous job, or by whatever was in the repository when it
    arrived — and a push then goes wherever that says.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return f"{url!r} is not a URL"
    if parts.scheme not in ("https", "http"):
        return f"{url!r} is not an https remote"
    if parts.username or parts.password:
        return "that remote has a credential embedded in it"
    if (parts.hostname or "").lower() != forge.host.lower():
        return f"that remote points at {parts.hostname!r}, not {forge.host!r}"
    return ""
