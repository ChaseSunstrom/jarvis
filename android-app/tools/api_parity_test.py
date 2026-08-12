#!/usr/bin/env python3
"""Executable spec: every jarvis-core path the app calls answers on the console too.

## The bug, three times

The app can be pointed at **either** jarvis-core or the console — it has a whole
`ServerKind` for the difference, and the console is the address people actually
type, because it is the one with a web page on it. So every path the app asks
for has to exist on both. Three times it did not:

  * `POST /api/voice/speaker/enrol` — reported as *"with the teach voice thing,
    it says 'Could not reach Jarvis'"*. It was a 404 from a server answering in
    20 ms.
  * `POST /api/voice/speaker/verify` — the same gap, found beside it.
  * `POST /api/pair/claim` — reported as *"when scanning the QR code, it says
    'that url has no endpoint'"*. The QR's address defaults to the console's own
    origin, which is right; the claim endpoint lived only on jarvis-core, so
    scanning it could never work.

Each was a separate report, days apart, each looked like its own feature being
broken, and each was one missing file. That is the shape worth automating: the
list of paths is short, it is in the Kotlin, and it changes rarely.

## What this checks, and what it cannot

It reads the `/api/...` string literals out of the app's own source and asks
whether a SvelteKit route exists for each. It cannot tell whether that route
does the right thing — `routes.test.ts` is where each proxy says what stops it
being a way to reach the backend with the admin token — only whether asking for
it returns a page instead of an answer.

Paths the app never composes itself are exempt, and each exemption says why. An
unlisted, unmatched path fails: the whole point is that a NEW path cannot be
added on one side only.

Run:  python3 android-app/tools/api_parity_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
ROOT = ANDROID.parent
KOTLIN = ANDROID / "app" / "src" / "main" / "kotlin"
CONSOLE_ROUTES = ROOT / "jarvis-web" / "src" / "routes" / "api"
WS_PROXY = ROOT / "jarvis-web" / "server" / "ws-proxy.js"

#: Paths the app names but does not fetch from the console, and why.
#:
#: Every entry is a decision. A path that merely has not been implemented yet
#: does NOT belong here — that is the bug this file exists to catch.
EXEMPT: dict[str, str] = {
    "/api/websocket": (
        "jarvis-core's socket path, chosen by ServerKind.CORE. Against the "
        "console the app uses ServerKind.RELAY's `/ws`, which server/ws-proxy.js "
        "serves — a different path on purpose, not a missing one."
    ),
}

#: A dynamic segment in a SvelteKit route directory: `models/[name]`.
_PARAM = re.compile(r"\[[^\]]+\]")


def kotlin_files() -> list[Path]:
    return sorted(KOTLIN.rglob("*.kt"))


def code_of(path: Path) -> str:
    """Kotlin with comments stripped.

    Load-bearing: half the `/api/...` mentions in this app are prose. A KDoc
    saying "this used to hardcode /api/websocket" is not a call.
    """
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def strings_in(source: str) -> str:
    """The contents of every double-quoted string, joined.

    Scanning the whole file would pick up paths out of ordinary identifiers and,
    worse, out of the KDoc this function's caller has already stripped. Scanning
    only string bodies keeps it to things the app can actually send.
    """
    return "\n".join(re.findall(r'"((?:\\.|[^"\\])*)"', source))


def requested_paths() -> dict[str, str]:
    """`path -> the file that names it`, from string literals in shipping code."""
    found: dict[str, str] = {}
    for path in kotlin_files():
        # Anywhere inside a string, not only as the whole of one. Half these
        # paths are composed — `"${payload.url.trimEnd('/')}/api/pair/claim"` —
        # and a regex anchored to the opening quote finds the plain ones only,
        # which is to say it finds the paths least likely to be the problem.
        for literal in re.findall(r'(/api/[A-Za-z0-9/_-]*)', strings_in(code_of(path))):
            found.setdefault(literal, str(path.relative_to(ROOT)))
    return found


def console_routes() -> set[str]:
    """`/api/...` paths the console serves, dynamic segments left as-is."""
    out: set[str] = set()
    if not CONSOLE_ROUTES.is_dir():
        return out
    for server in CONSOLE_ROUTES.rglob("+server.ts"):
        rel = server.parent.relative_to(CONSOLE_ROUTES).as_posix()
        out.add("/api" if rel == "." else f"/api/{rel}")
    return out


def matches(path: str, routes: set[str]) -> bool:
    """Whether [path] would reach one of [routes].

    A trailing slash on the app's side means "and then something" — `/api/models/`
    is a prefix it appends a name to — so it matches a route with a dynamic
    segment there.
    """
    wanted = path.rstrip("/")
    for route in routes:
        if _PARAM.sub("*", route).rstrip("/") == wanted:
            return True
        # `/api/models/` vs route `/api/models/[name]`
        if path.endswith("/") and route.startswith(wanted + "/"):
            return True
    return False


def test_the_app_and_the_console_have_not_drifted() -> None:
    routes = console_routes()
    assert routes, f"no console routes found under {CONSOLE_ROUTES}"
    missing = [
        f"{p} (called from {where})"
        for p, where in sorted(requested_paths().items())
        if p not in EXEMPT and not matches(p, routes)
    ]
    assert not missing, (
        "the app asks for these and the console does not answer them, so a phone "
        "paired to the console gets a 404 from a server that is working "
        "perfectly — which is what 'Could not reach Jarvis' and 'that URL has no "
        f"endpoint' both were: {missing}"
    )


def test_every_exemption_is_still_needed() -> None:
    """An exemption for a path the app no longer names, or that the console has
    since grown a route for, is a lie waiting to cover the next gap."""
    named = set(requested_paths())
    routes = console_routes()
    stale = sorted(p for p in EXEMPT if p not in named)
    assert not stale, f"EXEMPT lists paths the app never asks for: {stale}"
    now_served = sorted(p for p in EXEMPT if matches(p, routes))
    assert not now_served, (
        f"these are exempt AND served; delete the exemption: {now_served}"
    )


def test_the_relay_socket_the_exemption_relies_on_exists() -> None:
    """`/api/websocket` is exempt because the relay answers on `/ws` instead.
    If that stops being true the exemption is hiding a real gap."""
    assert WS_PROXY.is_file(), f"missing {WS_PROXY}"
    proxy = WS_PROXY.read_text(encoding="utf-8")
    assert "'/ws'" in proxy or '"/ws"' in proxy, (
        "the relay no longer serves /ws, so ServerKind.RELAY has nowhere to "
        "connect and the /api/websocket exemption is unfounded"
    )
    endpoint = code_of(KOTLIN / "ai" / "jarvis" / "app" / "config" / "ServerEndpoint.kt")
    assert '"/ws"' in endpoint, "ServerKind.RELAY no longer uses /ws"


def test_the_three_paths_this_file_was_written_for_are_served() -> None:
    """Named individually, because a generic check that happened to pass while
    one of them regressed would be the same failure a third time."""
    routes = console_routes()
    for path in ("/api/pair/claim", "/api/voice/speaker/enrol", "/api/voice/speaker/verify"):
        assert matches(path, routes), f"the console has stopped answering {path}"


def main() -> int:
    tests = [
        (n, f) for n, f in sorted(globals().items())
        if n.startswith("test_") and callable(f)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # a broken check is a failure, not an abort
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({len(requested_paths())} paths named, {len(console_routes())} console routes)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
