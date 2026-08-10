#!/usr/bin/env python3
"""Executable spec for telling jarvis-core and jarvis-web apart.

`app/src/main/kotlin/ai/jarvis/app/config/ServerEndpoint.kt` decides which of
the two servers is at the URL the user typed, and therefore which WebSocket
path to dial and whether this end has to authenticate.

That decision is why voice used to work only inside the management WebView.
The console (jarvis-web) serves its socket at `/ws`; jarvis-core serves
`/api/websocket`. Assuming jarvis-core against a console URL failed twice over:
wrong path, and — because the relay then injected its own token and swallowed
the handshake — a client waiting forever for an `auth_ok` that never came.

The relay now passes a PRESENTED token through to jarvis-core instead of
injecting for everyone, so the handshake is identical on both kinds and only
the path differs. That is both the security fix (an unauthenticated client no
longer gets the admin token's power over the house) and what makes a single
URL work whichever server is behind it.

Checked here:

  1. The discrimination rule, mirrored below, agrees with a hand-written table
     of probe responses. Written out by hand so a bug in "the algorithm" cannot
     hide in both copies.
  2. Real response bodies from both servers are used as fixtures, taken from
     `jarvis-web/src/routes/api/config/+server.ts` and jarvis-core's
     `config_payload`, so the keys being keyed on actually exist.
  3. The Kotlin still contains the rule and the two paths — the structural
     check the other mirrors in this directory use.

Run:  python3 android-app/tools/server_endpoint_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# --- the rules, mirrored from ServerEndpoint.kt ----------------------------

CORE_PATH = "/api/websocket"
RELAY_PATH = "/ws"

#: Keys only jarvis-web's public client config has.
RELAY_KEYS = ("backendUrlVar", "tokenConfigured")
#: Keys only jarvis-core's Home Assistant-shaped config has. `version` is
#: deliberately NOT one of them: both servers could plausibly report a version.
CORE_KEYS = ("components", "ha_version")


def kind_from_probe(status: int, body: str | None) -> str | None:
    """Mirror of ServerEndpoint.kindFromProbe. Returns 'CORE', 'RELAY' or None."""
    if body is not None and body.strip():
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            if any(key in parsed for key in RELAY_KEYS):
                return "RELAY"
            if any(key in parsed for key in CORE_KEYS):
                return "CORE"
    if status in (401, 403):
        return "CORE"
    return None


def ws_path(kind: str) -> str:
    return CORE_PATH if kind == "CORE" else RELAY_PATH


def client_authenticates(kind: str) -> bool:
    """Both, now.

    jarvis-web's relay used to inject its own admin token and swallow the
    handshake, so this end had to skip it there. It now passes a presented
    token through to jarvis-core instead — which is both the security fix (an
    unauthenticated client no longer gets the admin token's power) and what
    makes one URL work for either server.
    """
    return True


def candidates(known: str | None) -> list[str]:
    if known is None:
        return ["RELAY", "CORE"]
    return [known] + [k for k in ("CORE", "RELAY") if k != known]


# --- fixtures: what each server actually answers ---------------------------

#: jarvis-web/src/routes/api/config/+server.ts, unauthenticated.
RELAY_BODY = json.dumps(
    {
        "pipeline": "Jarvis",
        "ttsVoice": "en_GB-alan-medium",
        "backend": "core",
        "backendUrl": "http://127.0.0.1:8080",
        "backendUrlVar": "JARVIS_URL",
        "backendTokenVar": "JARVIS_TOKEN",
        "tokenConfigured": True,
        "problem": None,
    }
)

#: jarvis-core's /api/config, once a bearer token is attached.
CORE_BODY = json.dumps(
    {
        "location_name": "Home",
        "version": "0.1.0",
        "ha_version": "jarvis-0.1.0",
        "components": ["automation", "light", "llm"],
        "unit_system": {"length": "km"},
        "time_zone": "Europe/London",
    }
)

#: jarvis-core refusing an unauthenticated probe.
CORE_401_BODY = json.dumps({"detail": "Not authenticated"})


CASES = [
    # (label, status, body, expected kind)
    ("the console, answering its public config", 200, RELAY_BODY, "RELAY"),
    ("jarvis-core, once authenticated", 200, CORE_BODY, "CORE"),
    ("jarvis-core, refusing the probe", 401, CORE_401_BODY, "CORE"),
    ("jarvis-core, refusing with 403", 403, "", "CORE"),
    # A reverse proxy that rewrites the status must not flip the answer: the
    # body still says which server it came from.
    ("the console behind a proxy that rewrote 200 to 401", 401, RELAY_BODY, "RELAY"),
    ("jarvis-core behind a proxy that rewrote 401 to 200", 200, CORE_BODY, "CORE"),
    # Nothing usable: keep believing whatever we already believed.
    ("an unrelated server", 200, json.dumps({"hello": "world"}), None),
    ("a captive portal serving HTML", 200, "<html>sign in</html>", None),
    ("an empty body and an ordinary status", 200, "", None),
    ("a 404 from something that is not Jarvis", 404, "", None),
    ("no body at all", 500, None, None),
    # `version` alone is not enough to claim jarvis-core.
    ("something that merely reports a version", 200, json.dumps({"version": "9"}), None),
]


def check_rules() -> int:
    failures = 0
    for label, status, body, expected in CASES:
        got = kind_from_probe(status, body)
        if got != expected:
            print(f"FAIL  {label}: expected {expected}, got {got}")
            failures += 1
    return failures


def check_paths() -> int:
    """The two paths must be different and each must belong to one server."""
    failures = 0
    if ws_path("CORE") == ws_path("RELAY"):
        print("FAIL  both kinds dial the same path; the distinction does nothing")
        failures += 1
    for kind in ("CORE", "RELAY"):
        if not client_authenticates(kind):
            print(f"FAIL  {kind} must authenticate from this end")
            failures += 1
    return failures


def check_candidate_order() -> int:
    failures = 0
    if candidates(None) != ["RELAY", "CORE"]:
        print(f"FAIL  unknown-kind order is {candidates(None)}")
        failures += 1
    for known in ("CORE", "RELAY"):
        order = candidates(known)
        if order[0] != known:
            print(f"FAIL  a known kind must be tried first, got {order}")
            failures += 1
        if sorted(order) != ["CORE", "RELAY"]:
            print(f"FAIL  every kind must remain reachable, got {order}")
            failures += 1
    return failures


def check_kotlin_agrees(root: Path) -> int:
    """The Kotlin still says what this file says."""
    src = root / "app/src/main/kotlin/ai/jarvis/app/config/ServerEndpoint.kt"
    if not src.is_file():
        print(f"FAIL  {src} is missing")
        return 1
    text = src.read_text(encoding="utf-8")
    failures = 0

    for literal in (f'"{CORE_PATH}"', f'"{RELAY_PATH}"'):
        if literal not in text:
            print(f"FAIL  ServerEndpoint.kt no longer mentions {literal}")
            failures += 1

    for key in RELAY_KEYS:
        if f'"{key}"' not in text:
            print(f"FAIL  ServerEndpoint.kt no longer keys on {key!r}")
            failures += 1
    if not any(f'"{key}"' in text for key in CORE_KEYS):
        print(f"FAIL  ServerEndpoint.kt keys on none of {CORE_KEYS}")
        failures += 1

    # The relay must NOT be declared as authenticating on our behalf any more.
    # If that flag comes back, the client would skip the handshake against a
    # server that now expects it, and hang.
    if "clientAuthenticates" in text:
        print(
            "FAIL  ServerEndpoint.kt still has clientAuthenticates; the relay "
            "passes our token through now, so both kinds handshake identically"
        )
        failures += 1
    return failures


def check_web_fixture_is_real(repo: Path) -> int:
    """The keys we key on must exist in the console's actual response.

    Guards the way this could silently rot: someone renames a field in
    +server.ts, the phone stops recognising the console, and voice quietly
    goes back to only working in the WebView.
    """
    src = repo / "jarvis-web/src/routes/api/config/+server.ts"
    if not src.is_file():
        print(f"FAIL  {src} is missing")
        return 1
    text = src.read_text(encoding="utf-8")
    failures = 0
    for key in RELAY_KEYS:
        if key not in text:
            print(f"FAIL  jarvis-web no longer returns {key!r}, which the phone keys on")
            failures += 1
    return failures


def main() -> int:
    here = Path(__file__).resolve()
    android = here.parents[1]
    repo = here.parents[2]

    failures = (
        check_rules()
        + check_paths()
        + check_candidate_order()
        + check_kotlin_agrees(android)
        + check_web_fixture_is_real(repo)
    )
    total = len(CASES) + 3 + 3 + 6 + len(RELAY_KEYS)
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        f"server endpoint: {len(CASES)} probe cases, the two socket paths, the "
        "Kotlin and the console's own response all agree"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
