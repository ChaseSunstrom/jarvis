#!/usr/bin/env python3
"""Executable spec for the pairing-QR payload parser.

`app/src/main/kotlin/ai/jarvis/app/config/PairingPayload.kt` decides what a
camera pointed at an unknown square is allowed to hand the rest of the app.
That is a security boundary — it ends with the phone dialling an address it was
told to trust — so it is written down twice: once in Kotlin, which this
container cannot compile, and once here, where it runs.

Three things are checked:

  1. The rules, mirrored below, agree with an explicit table of payloads and
     verdicts. The table is written out by hand, so a bug in "the algorithm"
     cannot hide in both copies of it.
  2. One canonical payload string is asserted byte for byte. That exact string
     is also a fixture in the web console's `buildPairingUri` test, which is
     what stops the producer and the consumer drifting apart across two
     languages and two repositories' worth of assumptions.
  3. The Kotlin source still contains the rules — a cheap structural check that
     catches someone editing one copy and forgetting the other, which is the
     failure `policy_truth_table_test.py` already exists to prevent.

Run:  python3 android-app/tools/pairing_payload_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

# --- the rules, mirrored from PairingPayload.kt ----------------------------

SCHEME = "jarvis"
AUTHORITY = "pair"
VERSION = "1"
MAX_LENGTH = 512
CODE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

#: Suffixes ServerUrl.isPrivateHost tolerates for cleartext. Mirrored only as
#: far as this parser needs: the point here is that the QR path runs the SAME
#: check as the typed path, not that this file re-derives that check.
PRIVATE_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".home")

OK = "ok"
NOT_A_PAYLOAD = "not-a-payload"
REFUSED = "refused"


def is_private_host(host: str) -> bool:
    host = host.lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    if host.endswith(PRIVATE_SUFFIXES):
        return True
    if host.startswith(("10.", "192.168.")):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


def has_control_char(text: str) -> bool:
    return any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in text)


def query_params(raw_query: str) -> dict[str, str] | None:
    """Single-valued parameters, or None on a repeat. See the Kotlin docstring."""
    if not raw_query:
        return None
    out: dict[str, str] = {}
    for part in raw_query.split("&"):
        if not part:
            continue
        eq = part.find("=")
        if eq <= 0:
            return None
        key = part[:eq]
        if key in out:
            return None
        out[key] = part[eq + 1 :]
    return out


def parse(raw: str | None) -> tuple[str, str]:
    """(verdict, detail). Mirrors PairingPayload.parse."""
    text = (raw or "").strip()
    if not text:
        return NOT_A_PAYLOAD, "empty"
    if len(text) > MAX_LENGTH:
        return REFUSED, "too long"
    if has_control_char(text):
        return REFUSED, "control character"
    if not text.lower().startswith(f"{SCHEME}://"):
        return NOT_A_PAYLOAD, "not a jarvis:// url"

    split = urlsplit(text)
    if split.netloc.lower() != AUTHORITY:
        return NOT_A_PAYLOAD, "not a pairing url"

    params = query_params(split.query)
    if params is None:
        return REFUSED, "unreadable query"

    if params.get("v") != VERSION:
        return REFUSED, "version"

    raw_url = params.get("u") or ""
    if not raw_url:
        return REFUSED, "no url"
    decoded = unquote(raw_url)
    if has_control_char(decoded):
        return REFUSED, "control character"

    # ServerUrl.check, as far as this boundary depends on it.
    candidate = decoded.strip().rstrip("/")
    if "://" not in candidate:
        candidate = "http://" + candidate
    target = urlsplit(candidate)
    if target.scheme not in ("http", "https"):
        return REFUSED, "scheme"
    if not target.hostname:
        return REFUSED, "no host"
    if target.scheme == "http" and not is_private_host(target.hostname):
        return REFUSED, "cleartext to a public host"

    code = params.get("c") or ""
    if not CODE.match(code):
        return REFUSED, "code"

    return OK, candidate


# --- the table -------------------------------------------------------------

GOOD_CODE = "7QK29F4MXZ1Tabcd"

#: The canonical payload, asserted byte for byte. The web console's
#: `buildPairingUri` test asserts this same string; if either side changes the
#: parameter order, the encoding, or the version, exactly one of the two tests
#: goes red and the mismatch is found before a phone ever sees it.
FIXTURE = (
    "jarvis://pair?v=1&u=http%3A%2F%2F192.168.2.10%3A8080&c=" + GOOD_CODE
)

TABLE: list[tuple[str, str, str]] = [
    # payload, verdict, why it is in the table
    (FIXTURE, OK, "the canonical payload"),
    (
        "jarvis://pair?v=1&u=https%3A%2F%2Fjarvis.example.com&c=" + GOOD_CODE,
        OK,
        "https to a public host is fine",
    ),
    (
        "jarvis://pair?v=1&u=http%3A%2F%2Fjarvis.local%3A8080&c=" + GOOD_CODE,
        OK,
        "cleartext to a .local host is what most installs are",
    ),
    # --- not addressed to us: the caller may fall back to a bare token -----
    ("", NOT_A_PAYLOAD, "empty scan"),
    ("just-a-bare-token-like-before", NOT_A_PAYLOAD, "the old paste-a-token path"),
    ("https://example.com/", NOT_A_PAYLOAD, "someone else's QR"),
    ("jarvis://other?v=1", NOT_A_PAYLOAD, "our scheme, not our authority"),
    # --- ours, and refused --------------------------------------------------
    (
        "jarvis://pair?v=2&u=http%3A%2F%2F192.168.2.10%3A8080&c=" + GOOD_CODE,
        REFUSED,
        "a future version must not be guessed at",
    ),
    (
        "jarvis://pair?u=http%3A%2F%2F192.168.2.10%3A8080&c=" + GOOD_CODE,
        REFUSED,
        "no version at all",
    ),
    (
        "jarvis://pair?v=1&u=javascript%3Aalert(1)&c=" + GOOD_CODE,
        REFUSED,
        "a scheme that is not http(s)",
    ),
    (
        "jarvis://pair?v=1&u=http%3A%2F%2Fevil.example.com&c=" + GOOD_CODE,
        REFUSED,
        "cleartext to a public host — the QR must not relax the typed path's rule",
    ),
    (
        "jarvis://pair?v=1&u=http%3A%2F%2F192.168.2.10%3A8080&c=short",
        REFUSED,
        "a code too short to be one of ours",
    ),
    (
        "jarvis://pair?v=1&u=http%3A%2F%2F192.168.2.10%3A8080&c=has%20a%20space",
        REFUSED,
        "a code outside base64url",
    ),
    (
        "jarvis://pair?v=1&u=http%3A%2F%2F192.168.2.10%3A8080",
        REFUSED,
        "no code",
    ),
    (
        "jarvis://pair?v=1&c=" + GOOD_CODE,
        REFUSED,
        "no url",
    ),
    (
        "jarvis://pair?v=1&u=http%3A%2F%2F192.168.2.10%3A8080&u=http%3A%2F%2Fevil.example.com&c="
        + GOOD_CODE,
        REFUSED,
        "two urls: never guess which one the other reader took",
    ),
    (
        "jarvis://pair?v=1&u=http%3A%2F%2F192.168.2.10%3A8080\n&c=" + GOOD_CODE,
        REFUSED,
        "a control character anywhere in the payload",
    ),
    (
        "jarvis://pair?v=1&u=http%3A%2F%2F192.168.2.10%3A8080&c=" + "a" * 600,
        REFUSED,
        "longer than any legitimate payload",
    ),
]


def check_table() -> list[str]:
    failures = []
    for payload, expected, why in TABLE:
        verdict, detail = parse(payload)
        if verdict != expected:
            shown = payload if len(payload) <= 70 else payload[:67] + "..."
            failures.append(
                f"{shown!r}\n    expected {expected}, got {verdict} ({detail})\n    ({why})"
            )
    return failures


def check_fixture() -> list[str]:
    """The canonical string, and what it parses to."""
    failures = []
    verdict, normalized = parse(FIXTURE)
    if verdict != OK:
        failures.append(f"the canonical fixture does not parse: {verdict} ({normalized})")
    elif normalized != "http://192.168.2.10:8080":
        failures.append(f"fixture normalises to {normalized!r}, expected 'http://192.168.2.10:8080'")
    # Pin the shape itself, so a reordering is caught here rather than by a
    # phone failing to scan a code six weeks from now.
    if not FIXTURE.startswith("jarvis://pair?v=1&u="):
        failures.append("the fixture's parameter order changed")
    return failures


def check_kotlin_still_says_so() -> list[str]:
    """Cheap structural check that the Kotlin copy has not drifted."""
    source = (
        Path(__file__).resolve().parents[1]
        / "app/src/main/kotlin/ai/jarvis/app/config/PairingPayload.kt"
    )
    if not source.exists():
        return [f"missing {source}"]
    text = source.read_text(encoding="utf-8")
    required = {
        'SCHEME = "jarvis"': "the scheme",
        'AUTHORITY = "pair"': "the authority",
        'VERSION = "1"': "the version",
        "MAX_LENGTH = 512": "the length cap",
        '[A-Za-z0-9_-]{16,64}': "the code charset",
        "ServerUrl.check(decoded)": "reuse of the typed path's validator",
        "isIsoControl()": "the control-character refusal",
    }
    return [f"PairingPayload.kt no longer contains {what} ({snippet!r})"
            for snippet, what in required.items() if snippet not in text]


def main() -> int:
    failures = check_table() + check_fixture() + check_kotlin_still_says_so()
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} failed", file=sys.stderr)
        return 1
    print(f"pairing payload: {len(TABLE)} cases, the canonical fixture, and the Kotlin mirror all agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
