"""Borrow the running Chromium, rather than pretending to be one.

`jarvis-browser` is the only browser in this system: the research engine fetches
through it, and its Playwright Chromium is the thing that can read a page whose
text arrives from JavaScript. `testing/live/fixture_browser.py` is a stand-in
for the two routes that service exposes — useful on a host with no stack, and
proof of nothing about the browser itself.

The reason the stand-in used to be the default is a good one: the real service
refuses loopback and RFC1918 addresses (`safety.is_blocked_host`), and this
repository's fixture web is served on 127.0.0.2 and 127.0.0.3. That refusal is
correct and must stay — so instead of weakening it, a run brings the container
back with those two addresses in the operator's own LAN exemption
(`BROWSER_LAN_ALLOWLIST`) and puts the setting back afterwards.

    with SharedBrowser() as browser:
        if browser.url:
            ... the real Chromium, allowed to read the fixture web ...

What it will not do: run if the container is not up, mint a token of its own, or
leave the exemption in place. Each of those is checked before it borrows and
undone after, and if any of them is not true it says so and the caller falls
back to the stand-in — never silently, because "the browser test passed" while
talking to a fake is the failure this module exists to prevent.
"""

from __future__ import annotations

import os
from pathlib import Path

from .stack import Stack, docker_available

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The compose service, its container name, and where it listens. The port is
#: `network_mode: host`, so this is the host's own loopback.
SERVICE = "jarvis-browser"
URL = "http://127.0.0.1:8210"

#: The addresses `FixtureWeb` binds, in the order it assigns them. Static on
#: purpose: the ports are random and the hosts are not, and a host-only
#: exemption is the narrowest thing that can be written here.
FIXTURE_HOSTS = ("127.0.0.2", "127.0.0.3")


def browser_token() -> str:
    """The operator's own token, from the environment or their `.env`.

    Read rather than minted, for the same reason `live_credentials()` reads the
    core's: a suite that issues itself a credential is testing a door nobody
    else comes through.
    """
    token = os.environ.get("JARVIS_BROWSER_TOKEN", "").strip()
    if token:
        return token
    for name in ("jarvis-core/.env", ".env"):
        path = REPO_ROOT / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.strip().partition("=")
            if key.strip() == "JARVIS_BROWSER_TOKEN":
                token = value.strip().strip('"').strip("'")
                if token:
                    return token
    return ""


class SharedBrowser:
    """The running `jarvis-browser`, lent to a run and given back."""

    def __init__(self, hosts: tuple[str, ...] = FIXTURE_HOSTS) -> None:
        self.hosts = hosts
        self.url = ""
        self.token = ""
        #: Why it could not be borrowed, in a sentence, for the caller to print.
        self.why = ""
        self._borrowed = False
        self.stack = Stack()

    def available(self) -> str:
        """"" if it can be borrowed, else the reason it cannot."""
        if os.environ.get("LIVE_SHARED_BROWSER", "1").strip() in ("0", "no", "false"):
            # The switch exists to make the fallback testable: a scenario that
            # needs JavaScript must FAIL against the stand-in, and the only way
            # to know it does is to run it that way on purpose.
            return "LIVE_SHARED_BROWSER=0 asked for the fixture stand-in"
        if not docker_available():
            return "docker is not available on this host"
        try:
            health = self.stack.health_of(SERVICE)
        except Exception as err:  # noqa: BLE001 - not running is an answer
            return f"{SERVICE} is not running ({err})"
        if health not in ("healthy", ""):
            return f"{SERVICE} is {health or 'not running'}"
        if not browser_token():
            return "JARVIS_BROWSER_TOKEN is not set (it is in jarvis-core/.env)"
        return ""

    def start(self) -> str:
        """Recreate the service with the fixture hosts exempted. Returns its URL."""
        self.why = self.available()
        if self.why:
            return ""
        # The operator's own value is whatever `.env` says; adding to it here
        # would need parsing their list and merging, and a run that is about to
        # put the setting back is better off saying exactly what it wants.
        self.stack.recreate(SERVICE, {"BROWSER_LAN_ALLOWLIST": ",".join(self.hosts)})
        self._borrowed = True
        self.url = URL
        self.token = browser_token()
        return self.url

    def stop(self) -> None:
        """Put the exemption back the way their `.env` describes it."""
        if not self._borrowed:
            return
        self._borrowed = False
        self.url = self.token = ""
        # Empty rather than absent: this process may have inherited a value,
        # and compose reads the environment before it reads `.env`. Empty is
        # also what `.env` ships, and it is the safe direction to be wrong in —
        # an exemption left behind is a guard quietly weakened.
        self.stack.recreate(SERVICE, {"BROWSER_LAN_ALLOWLIST": ""})

    def __enter__(self) -> "SharedBrowser":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()
