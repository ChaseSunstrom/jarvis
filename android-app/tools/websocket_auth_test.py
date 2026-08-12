#!/usr/bin/env python3
"""Executable spec: every WebSocket this app opens presents the bearer token.

The bug this exists for, from a real logcat:

    W JarvisCompanionVoice: companion voice socket failed
    W JarvisCompanionVoice: java.net.ProtocolException:
        Expected HTTP 101 response but was '401 Unauthorized'
        at okhttp3.internal.ws.RealWebSocket.checkUpgradeSuccess$okhttp
    ... repeated, once per proactive message

`CompanionVoiceClient` authenticated *in band* — it waited for jarvis-core's
`{"type":"auth_required"}` and answered with the token — and opened the socket
with no headers at all. Against jarvis-core directly that works. Against
jarvis-web's relay, which is the URL a person actually types and the first
candidate the app tries, the upgrade itself is authenticated: no header, 401,
and not one frame is ever exchanged, so the in-band reply that holds the token
never runs.

What made it expensive to find is that **nothing looked broken**. A companion
voice client that cannot connect calls back with null, and the caller falls
back to posting a notification — which is the correct, designed behaviour for
"no surface can speak right now". So every proactive line and every spoken
answer silently downgraded to a notification, exactly as if that had been a
policy decision, and the only evidence was a retry loop in logcat.

Two other clients in this app had already learned this the hard way, and their
KDoc says so. That is the argument for a spec rather than a third fix: three
clients, three chances to forget, one rule.

    JarvisChannel          .header("Authorization", "Bearer ${cfg.token}")
    AssistPipelineClient   .header("Authorization", "Bearer $token")
    CompanionVoiceClient   .header("Authorization", "Bearer $token")   <- was missing

Run:  python3 android-app/tools/websocket_auth_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
MAIN_KOTLIN = APP / "src" / "main" / "kotlin"
COMPANION = MAIN_KOTLIN / "ai" / "jarvis" / "app" / "companion" / "CompanionVoiceClient.kt"
ENDPOINT = MAIN_KOTLIN / "ai" / "jarvis" / "app" / "config" / "ServerEndpoint.kt"


def kotlin_files() -> list[Path]:
    return sorted(MAIN_KOTLIN.rglob("*.kt"))


def code_only(src: str) -> str:
    """Kotlin with whole-line comments dropped, so KDoc quoting a call that
    must not appear is not itself a violation."""
    return "\n".join(
        line
        for line in src.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*", "*/"))
    )


#: `http.newWebSocket(` and everything up to the matching close, near enough:
#: OkHttp's builder chain for one call never spans more than this, and taking
#: too much only risks a false PASS on a file that has two sockets in a row —
#: which is why the count of call sites is asserted separately.
NEW_WEBSOCKET = re.compile(r"newWebSocket\s*\(")


def websocket_call_sites() -> list[tuple[Path, int, str]]:
    """(file, line, the request-building text) for every socket the app opens."""
    sites: list[tuple[Path, int, str]] = []
    for path in kotlin_files():
        source = code_only(path.read_text(encoding="utf-8"))
        for match in NEW_WEBSOCKET.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            # From the call to the end of the argument list. Balanced-paren
            # scan rather than a fixed window: the builder chains differ in
            # length by a factor of three across the three call sites.
            depth = 0
            end = match.end()
            for index in range(match.end() - 1, len(source)):
                if source[index] == "(":
                    depth += 1
                elif source[index] == ")":
                    depth -= 1
                    if depth == 0:
                        end = index
                        break
            sites.append((path, line, source[match.start() : end]))
    return sites


def test_the_app_opens_the_sockets_we_think_it_does():
    """A count, so that a fourth client added later fails this file rather
    than quietly inheriting nothing."""
    sites = websocket_call_sites()
    files = sorted({path.name for path, _, _ in sites})
    assert files == [
        "AssistPipelineClient.kt",
        "CompanionVoiceClient.kt",
        "JarvisChannel.kt",
    ], f"the set of WebSocket clients changed: {files}"


def test_every_websocket_presents_the_bearer_token_on_the_upgrade():
    """The rule. In-band auth is not a substitute — the relay never gets far
    enough to ask."""
    offenders = []
    for path, line, text in websocket_call_sites():
        if 'header("Authorization"' not in text:
            offenders.append(f"{path.relative_to(APP)}:{line}")
    assert not offenders, (
        "a WebSocket is opened without an Authorization header. jarvis-web's "
        "relay authenticates the UPGRADE, so a client that only answers the "
        "in-band `auth_required` frame gets 401 before any frame is exchanged "
        "— and the caller's graceful fallback hides it as a notification.\n  "
        + "\n  ".join(offenders)
    )


def test_the_companion_client_falls_back_to_the_other_server_kind():
    """One dial is not enough when the kind has not been discovered.

    The channel runs a discovery loop; a one-shot voice client cannot, but it
    can afford a second dial, and trying only the first candidate made a wrong
    guess indistinguishable from a dead server.
    """
    source = COMPANION.read_text(encoding="utf-8")
    assert "ServerEndpoint.candidates(serverKind)" in source, (
        "CompanionVoiceClient no longer builds a candidate list; a single "
        "guess at the server kind is one 404 away from silence"
    )
    body = source.split("override fun onFailure(", 1)
    assert len(body) == 2, "CompanionVoiceClient no longer overrides onFailure"
    assert "400..499" in body[1][:1200], (
        "onFailure no longer retries the other server kind on a 4xx upgrade. "
        "That is the one failure that means 'wrong endpoint' rather than "
        "'no network'."
    )


def test_the_spoken_reply_is_fetched_through_the_media_proxy():
    """The second half of the same bug.

    jarvis-core answers a tts run with one of its own paths
    (`/api/tts_proxy/<token>.wav`). The console does not serve that path — it
    proxies media at `/api/tts?path=` with the server-held token attached. So
    resolving the URL against the origin and fetching it directly is a 404 when
    the relay is what we are talking to, and a 404 on the audio of a spoken
    reply is indistinguishable from silence.
    """
    source = code_only(COMPANION.read_text(encoding="utf-8"))
    assert "ServerEndpoint.mediaUrl(" in source, (
        "CompanionVoiceClient no longer routes the TTS URL through "
        "ServerEndpoint.mediaUrl, which is the only thing that knows the "
        "console serves that audio from a different path"
    )
    assert "ServerUrl.resolveOnServer(serverUrl, url)" not in source, (
        "the raw resolve is back in the tts-end branch; it skips the relay's "
        "media proxy"
    )


def test_media_url_still_rewrites_for_the_relay():
    """Guards the helper the fix leans on, so the two cannot drift apart."""
    source = ENDPOINT.read_text(encoding="utf-8")
    body = source.split("fun mediaUrl(", 1)
    assert len(body) == 2, "ServerEndpoint no longer declares mediaUrl()"
    body = body[1][:1400]
    assert "/api/tts?path=" in body, "mediaUrl no longer targets the relay's media proxy"
    assert "resolveOnServer" in body, (
        "mediaUrl no longer origin-checks the path it was handed. The pipeline "
        "is the component this project assumes can be prompt-injected, and "
        "'it told us to fetch this' is not a reason to send the bearer token "
        "somewhere else."
    )


def main() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
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
        f"({len(websocket_call_sites())} WebSocket call sites)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
