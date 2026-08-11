#!/usr/bin/env python3
"""Executable spec for the second half of pairing: code → token.

`PairingPayload` decides whether a scanned square is addressed to this app and
whether its address may be dialled at all; `pairing_payload_test.py` pins that.
This covers what happens next — the HTTP exchange in `PairingClaim.kt`, and the
branch in `SettingsActivity` that routes a scan to it.

Three things here are load-bearing and none of them is visible in a screenshot:

  * **The QR carries a code, not a token.** That is the entire reason this file
    exists rather than `tokenField.setText(scanned)`. A QR on a screen can be
    photographed from across a room and ends up in whatever captured it; a
    token in one is valid forever, a code is single-use and lives five minutes.
  * **A Refused payload must never fall through.** Three outcomes, and the
    dangerous mistake is treating "recognisably ours and unacceptable" as
    "somebody's hand-made token QR" — which would put a refused payload's text
    into the token field and call it success.
  * **The answer is a credential, so redirects are off.** The request carries
    no token, but the response does; a 30x could hand the code to another host,
    or supply a token from one.

Run:  python3 android-app/tools/pairing_claim_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

KOTLIN_CLAIM = "app/src/main/kotlin/ai/jarvis/app/config/PairingClaim.kt"
KOTLIN_SETTINGS = "app/src/main/kotlin/ai/jarvis/app/SettingsActivity.kt"
CORE_PAIRING = "jarvis-core/jarvis/api/pairing.py"
WEB_PAIRING = "jarvis-web/src/lib/components/Pairing.svelte"


def _read(path: Path) -> str:
    if not path.is_file():
        print(f"FAIL  {path} is missing")
        return ""
    return path.read_text(encoding="utf-8")


def check_the_claim_refuses_what_it_must(android: Path) -> int:
    src = _read(android / KOTLIN_CLAIM)
    if not src:
        return 1
    failures = 0

    # The answer to this request is a credential. A redirect must not decide
    # who receives the code, or who supplies the token.
    for setting in ("followRedirects(false)", "followSslRedirects(false)"):
        if setting not in src:
            print(f"FAIL  PairingClaim no longer sets {setting}; a 30x could move the exchange")
            failures += 1

    # The same cleartext rule the typed field obeys. A printed address must not
    # be the way around it.
    if "LanHost.checkUrl(payload.url" not in src:
        print("FAIL  the scanned address is no longer checked against the cleartext rule")
        failures += 1
    if not re.search(r"if \(!verdict\.allowed\) \{\s*\n\s*return Result\.Failed", src):
        print("FAIL  a refused transport verdict no longer stops the claim")
        failures += 1

    # The path is fixed here, not taken from the QR.
    if '"${payload.url.trimEnd(\'/\')}/api/pair/claim"' not in src:
        print("FAIL  the claim path is no longer built from the validated URL alone")
        failures += 1

    # Nothing received is logged. A token or a code in logcat outlives the
    # five-minute window the whole design is built on.
    for leak in ("Log.w(TAG, t)", "token)", "payload.code"):
        for line in src.splitlines():
            if line.strip().startswith("Log.") and leak in line:
                print(f"FAIL  a log line carries {leak!r}")
                failures += 1
    return failures


def check_a_refused_payload_never_becomes_a_token(android: Path) -> int:
    """The dangerous branch, spelled out.

    `Refused` means "this claimed to be one of ours and is not acceptable".
    Falling through to the bare-token path would take a payload the parser
    just rejected and write it into the field the app authenticates with.
    """
    src = _read(android / KOTLIN_SETTINGS)
    if not src:
        return 1
    failures = 0

    branch = re.search(
        r"when \(val parsed = PairingPayload\.parse\(scanned\)\) \{(.*?)\n        \}",
        src,
        re.S,
    )
    if not branch:
        print("FAIL  the scan result no longer goes through PairingPayload.parse")
        return 1
    body = branch.group(1)

    for arm in ("Result.Ok", "Result.Refused", "Result.NotAPayload"):
        if f"is PairingPayload.{arm}" not in body:
            print(f"FAIL  the scan handler has no {arm} arm")
            failures += 1

    refused = re.search(r"is PairingPayload\.Result\.Refused ->(.*?)\n", body)
    if not refused:
        print("FAIL  cannot find the Refused arm")
        failures += 1
    elif "tokenField" in refused.group(1):
        print("FAIL  a REFUSED payload is written into the token field")
        failures += 1

    # Only the "not addressed to us" arm may fall back to the old behaviour.
    not_ours = re.search(r"is PairingPayload\.Result\.NotAPayload ->(.*?)\n            \}", body, re.S)
    if not not_ours or "tokenField.setText(scanned)" not in not_ours.group(1):
        print("FAIL  a plain token QR no longer works; that fallback is the older path")
        failures += 1
    return failures


def check_pairing_fills_both_fields(android: Path) -> int:
    """The address travels with the code, and that is half the point.

    Typing a token is bad; typing a token AND an address is why people give up.
    """
    src = _read(android / KOTLIN_SETTINGS)
    if not src:
        return 1
    block = re.search(r"is PairingClaim\.Result\.Ok -> \{(.*?)\n                    \}", src, re.S)
    if not block:
        print("FAIL  there is no success branch for a completed pairing")
        return 1
    body = block.group(1)
    failures = 0
    if "urlField.setText" not in body:
        print("FAIL  pairing does not fill in the server address")
        failures += 1
    if "tokenField.setText" not in body:
        print("FAIL  pairing does not fill in the token")
        failures += 1
    return failures


def check_the_three_ends_agree(root: Path) -> int:
    """Core mints it, the console draws it, the app parses it — one format."""
    failures = 0
    core = _read(root / CORE_PAIRING)
    web = _read(root / WEB_PAIRING)
    payload_kt = _read(
        root / "android-app/app/src/main/kotlin/ai/jarvis/app/config/PairingPayload.kt"
    )
    if not (core and web and payload_kt):
        return 1

    # The URL the console encodes must be the URL the app expects.
    if "jarvis://pair?v=1&u=" not in web:
        print("FAIL  the console no longer emits the v1 pairing URL the app parses")
        failures += 1
    for const, value in (("SCHEME", '"jarvis"'), ("AUTHORITY", '"pair"'), ("VERSION", '"1"')):
        if f"const val {const} = {value}" not in payload_kt:
            print(f"FAIL  PairingPayload.{const} is no longer {value}")
            failures += 1

    # The code the server mints has to match the alphabet and length the app
    # will accept, or every real pairing is refused by the phone.
    if "secrets.token_urlsafe(24)" not in core:
        print("FAIL  the server no longer mints a 24-byte urlsafe code")
        failures += 1
    code_re = re.search(r'private val CODE = Regex\("\^\[A-Za-z0-9_-\]\{(\d+),(\d+)\}\$"\)', payload_kt)
    if not code_re:
        print("FAIL  the app's code pattern changed shape; check it still accepts the server's")
        failures += 1
    else:
        low, high = int(code_re.group(1)), int(code_re.group(2))
        # token_urlsafe(24) is 32 base64url characters.
        if not (low <= 32 <= high):
            print(f"FAIL  the app accepts {low}..{high} characters; the server mints 32")
            failures += 1

    # And the single-use, short-lived properties the QR's safety rests on.
    if "CODE_TTL = 300.0" not in core:
        print("FAIL  a pairing code no longer expires in five minutes")
        failures += 1
    if "del self.codes[found.code]" not in core:
        print("FAIL  a pairing code is no longer spent when it is claimed")
        failures += 1

    # The escalation this whole endpoint would otherwise be. jarvis-web's relay
    # attaches the admin token to whatever connects, and its origin guard admits
    # a client sending no Origin because that is what a non-browser looks like —
    # so a script with transient reach to the console's port is already an
    # authenticated API client. Minting therefore needs a secret the relay does
    # not hold, or reach-for-a-minute becomes access-forever.
    # The constant moved to jarvis.auth, which is what mints and persists the
    # secret on first run; pairing.py re-exports it. Follow it there rather than
    # asserting where it used to live, or this check passes on the file having
    # been renamed and says nothing about the property.
    auth = _read(root / "jarvis-core/jarvis/auth.py")
    if 'ENV_PAIRING_SECRET = "JARVIS_PAIRING_SECRET"' not in auth:
        print("FAIL  minting no longer needs a second secret; the API token alone "
              "would be enough to turn LAN reach into a permanent token")
        failures += 1
    if "ENV_PAIRING_SECRET" not in core:
        print("FAIL  pairing.py no longer reads the pairing secret at all")
        failures += 1
    if "hmac.compare_digest(configured, str(offered or \"\"))" not in core:
        print("FAIL  the pairing secret is no longer compared in constant time")
        failures += 1
    # Matched without the argument list, because the box is threaded through it
    # now (`check_secret(..., jarvis)`) so the stored secret is reachable. What
    # must not change is that async_issue calls it at all: delete this line and
    # the API token alone mints a permanent credential.
    issue = re.search(r"async def async_issue\(.*?\n(?=\n?async def |\n?def )", core, re.S)
    if not issue:
        print("FAIL  async_issue is gone from pairing.py")
        failures += 1
    elif 'check_secret(' not in issue.group(0):
        print("FAIL  async_issue no longer checks the pairing secret")
        failures += 1

    rest = _read(root / "jarvis-core/jarvis/api/rest.py")
    if 'request.headers.get("origin")' not in rest:
        print("FAIL  a browser can claim a pairing code again; browsers always "
              "send Origin on a cross-origin POST and phones never do")
        failures += 1
    return failures


def main() -> int:
    android = Path(__file__).resolve().parents[1]
    root = android.parent
    failures = (
        check_the_claim_refuses_what_it_must(android)
        + check_a_refused_payload_never_becomes_a_token(android)
        + check_pairing_fills_both_fields(android)
        + check_the_three_ends_agree(root)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        "pairing claim: redirects off, the cleartext rule enforced on a scanned "
        "address, a refused payload never becomes a token, and all three ends "
        "agree on the format"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
