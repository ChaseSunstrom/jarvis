#!/usr/bin/env python3
"""Executable spec for choosing which GitHub release to install.

`app/src/main/kotlin/ai/jarvis/app/update/ReleaseFeed.kt` turns a JSON document
fetched over the internet into "download this file and ask the user to install
it". That is the most dangerous sentence in the app, so the rules it applies
are written down twice: once in Kotlin, which this container cannot compile,
and once here, where they run.

The rules, and why each exists:

  * Newer only. Android refuses a package whose versionCode is not greater than
    the installed one, so offering an equal-or-older release produces a prompt
    that can only fail. Compared as an integer, never as tag text — `v1.10.0`
    sorts before `v1.9.0` as a string.
  * https, to a GitHub host, matched exactly. A release body is
    attacker-controllable text if the repo is ever public or a token leaks, and
    "fetch whatever URL the JSON said" is how an updater becomes a malware
    delivery service. Host equality rather than suffix: `evilgithub.com` ends
    with `github.com`.
  * Drafts are not releases.

Run:  python3 android-app/tools/release_feed_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# --- the rules, mirrored from ReleaseFeed.kt -------------------------------

ALLOWED_HOSTS = {"github.com", "api.github.com", "objects.githubusercontent.com"}


def is_allowed_download(url: str) -> bool:
    text = url.strip()
    if not text.startswith("https://"):
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
        return False
    authority = text[len("https://") :].split("/")[0]
    if "@" in authority:
        return False
    host = authority.split(":")[0].lower()
    return host in ALLOWED_HOSTS


def _code_after_plus(text: str) -> int | None:
    plus = text.rfind("+")
    if plus < 0 or plus == len(text) - 1:
        return None
    digits = text[plus + 1 :].strip()
    if not digits or not digits.isdigit():
        return None
    value = int(digits)
    return value if value > 0 else None


def version_code_of(tag: str, label: str | None) -> int | None:
    return _code_after_plus(tag) or _code_after_plus(label or "")


def version_name_of(tag: str) -> str:
    stripped = tag[1:] if tag.startswith("v") else tag
    return stripped.split("+")[0] or tag


def pick(feed: str, installed: int, allow_prerelease: bool):
    try:
        releases = json.loads(feed)
    except (ValueError, TypeError):
        return None
    if not isinstance(releases, list):
        return None

    best = None
    for release in releases:
        if not isinstance(release, dict):
            continue
        if release.get("draft"):
            continue
        prerelease = bool(release.get("prerelease"))
        if prerelease and not allow_prerelease:
            continue
        tag = str(release.get("tag_name") or "").strip()
        if not tag:
            continue
        assets = release.get("assets")
        if not isinstance(assets, list):
            continue
        chosen = None
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            if not name.lower().endswith(".apk"):
                continue
            url = str(asset.get("browser_download_url") or "")
            if not is_allowed_download(url):
                continue
            code = version_code_of(tag, str(asset.get("label") or ""))
            if code is None:
                continue
            chosen = {"versionCode": code, "tag": tag, "url": url}
            break
        if chosen is None or chosen["versionCode"] <= installed:
            continue
        if best is None or chosen["versionCode"] > best["versionCode"]:
            best = chosen
    return best


# --- fixtures ---------------------------------------------------------------


def release(tag, *, code_in_tag=True, prerelease=False, draft=False,
            url=None, name="jarvis-release.apk", label=""):
    full_tag = tag
    asset_url = url if url is not None else (
        f"https://github.com/ChaseSunstrom/jarvis/releases/download/{tag}/{name}"
    )
    return {
        "tag_name": full_tag,
        "draft": draft,
        "prerelease": prerelease,
        "body": "notes",
        "assets": [
            {
                "name": name,
                "label": label,
                "size": 12345,
                "browser_download_url": asset_url,
            }
        ],
    }


def feed(*releases):
    return json.dumps(list(releases))


DOWNLOAD_CASES = [
    ("https://github.com/o/r/releases/download/v1/a.apk", True),
    ("https://objects.githubusercontent.com/x", True),
    ("https://api.github.com/x", True),
    # Not https.
    ("http://github.com/o/r/a.apk", False),
    ("ftp://github.com/a.apk", False),
    # Lookalike hosts: suffix matching would let all of these through.
    ("https://evilgithub.com/a.apk", False),
    ("https://github.com.evil.test/a.apk", False),
    ("https://notgithub.com/a.apk", False),
    # Credentials move the real host after the '@'.
    ("https://github.com@evil.test/a.apk", False),
    # Control characters.
    ("https://github.com/a\napk", False),
    ("", False),
]


def check_downloads() -> int:
    failures = 0
    for url, expected in DOWNLOAD_CASES:
        got = is_allowed_download(url)
        if got != expected:
            print(f"FAIL  is_allowed_download({url!r}) = {got}, expected {expected}")
            failures += 1
    return failures


def check_picking() -> int:
    failures = 0

    def expect(label, got, want):
        nonlocal failures
        if got != want:
            print(f"FAIL  {label}: got {got}, wanted {want}")
            failures += 1

    # Newer than installed -> offered.
    got = pick(feed(release("v1.2.0+42")), installed=41, allow_prerelease=True)
    expect("a newer build is offered", got and got["versionCode"], 42)

    # Same or older -> not offered. Android would refuse the install anyway.
    expect("the same build is not offered",
           pick(feed(release("v1.2.0+42")), 42, True), None)
    expect("an older build is not offered",
           pick(feed(release("v1.2.0+42")), 99, True), None)

    # Integer comparison, not string. As text "v1.9.0+9" > "v1.10.0+10".
    got = pick(feed(release("v1.9.0+9"), release("v1.10.0+10")), 8, True)
    expect("versions compare as integers", got and got["versionCode"], 10)

    # Newest code wins regardless of feed order.
    got = pick(feed(release("v1.10.0+10"), release("v1.11.0+11")), 1, True)
    expect("highest code wins, whatever the order", got and got["versionCode"], 11)

    # Drafts are never offered.
    expect("a draft is not offered",
           pick(feed(release("v2.0.0+50", draft=True)), 1, True), None)

    # Prereleases only when opted in.
    expect("a prerelease is withheld by default",
           pick(feed(release("v2.0.0+50", prerelease=True)), 1, False), None)
    got = pick(feed(release("v2.0.0+50", prerelease=True)), 1, True)
    expect("a prerelease is offered when opted in", got and got["versionCode"], 50)

    # A release with no readable code is skipped rather than guessed at.
    expect("a tag with no +code is skipped",
           pick(feed(release("v2.0.0")), 1, True), None)

    # The code may come from the asset label instead.
    got = pick(feed(release("nightly", label="build+77")), 1, True)
    expect("an asset label can carry the code", got and got["versionCode"], 77)

    # A hostile download URL disqualifies the release even if it is newest.
    expect("an off-host asset is refused",
           pick(feed(release("v9.9.9+999", url="https://evil.test/a.apk")), 1, True), None)

    # Non-APK assets are ignored.
    expect("a non-apk asset is not an update",
           pick(feed(release("v3.0.0+60", name="notes.txt")), 1, True), None)

    # Garbage in, nothing out.
    for bad in ("", "not json", "{}", "[1,2,3]", "null"):
        expect(f"garbage feed {bad!r}", pick(bad, 1, True), None)

    return failures


def check_version_text() -> int:
    failures = 0
    cases = [
        ("v1.2.3+42", 42, "1.2.3"),
        ("1.2.3+7", 7, "1.2.3"),
        ("v2.0.0", None, "2.0.0"),
        ("v1.2.3+", None, "1.2.3"),
        ("v1.2.3+abc", None, "1.2.3"),
        ("v1.2.3+0", None, "1.2.3"),  # 0 is not a usable versionCode
    ]
    for tag, code, name in cases:
        if version_code_of(tag, None) != code:
            print(f"FAIL  version_code_of({tag!r}) = {version_code_of(tag, None)}, expected {code}")
            failures += 1
        if version_name_of(tag) != name:
            print(f"FAIL  version_name_of({tag!r}) = {version_name_of(tag)!r}, expected {name!r}")
            failures += 1
    return failures


def check_kotlin_agrees(android: Path) -> int:
    src = android / "app/src/main/kotlin/ai/jarvis/app/update/ReleaseFeed.kt"
    if not src.is_file():
        print(f"FAIL  {src} is missing")
        return 1
    text = src.read_text(encoding="utf-8")
    failures = 0
    for host in ALLOWED_HOSTS:
        if f'"{host}"' not in text:
            print(f"FAIL  ReleaseFeed.kt no longer allows {host!r}")
            failures += 1
    for needed, why in [
        ('startsWith("https://")', "the https check"),
        ("draft", "the draft check"),
        ("prerelease", "the prerelease check"),
        ("isISOControl", "the control-character check"),
        ("contains('@')", "the embedded-credentials check"),
    ]:
        if needed not in text:
            print(f"FAIL  ReleaseFeed.kt lost {why} ({needed})")
            failures += 1
    # Host membership must be exact, not a suffix test.
    if "endsWith" in text and "host" in text.split("endsWith")[0][-200:]:
        print("FAIL  ReleaseFeed.kt appears to match hosts by suffix")
        failures += 1
    return failures


def check_workflow_tags_the_code(repo: Path) -> int:
    """CI must put the versionCode in the tag, or nothing can read it.

    The app cannot know a release's versionCode without downloading the APK,
    so the tag is the contract. If the workflow stops emitting `+<code>`, every
    release silently becomes un-offerable.
    """
    wf = repo / ".github/workflows/android-apk.yml"
    if not wf.is_file():
        print(f"FAIL  {wf} is missing")
        return 1
    text = wf.read_text(encoding="utf-8")
    if "JARVIS_VERSION_CODE" not in text:
        print("FAIL  the workflow no longer sets JARVIS_VERSION_CODE")
        return 1
    if not re.search(r"\+\$\{?\s*(JARVIS_)?VERSION_CODE", text) and "+$CODE" not in text:
        print("FAIL  the workflow's tag no longer carries +<versionCode>")
        return 1
    return 0


def main() -> int:
    here = Path(__file__).resolve()
    android = here.parents[1]
    repo = here.parents[2]

    failures = (
        check_downloads()
        + check_picking()
        + check_version_text()
        + check_kotlin_agrees(android)
        + check_workflow_tags_the_code(repo)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        f"release feed: {len(DOWNLOAD_CASES)} URL cases, the picking rules, the "
        "version-tag format, the Kotlin and the workflow all agree"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
