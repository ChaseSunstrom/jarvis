#!/usr/bin/env python3
"""Executable spec for the Android Auto surface.

## Why this exists

*"can you add complete functionality for android auto, with a view of Jarvis
on the display, similar to the web app view?"*

Nothing in CI can drive a head unit. The emulator suite cannot, the APK build
cannot, and a Desktop Head Unit needs a phone plugged into it. So the car
surface is the one part of this app whose behaviour is never executed by
anything automatic, which makes the properties that matter worth stating as
text — because the alternative is that they are checked once, by hand, on the
day they are written.

Four of them, and every one is a thing that fails silently:

1. **The host is validated.** A `CarAppService` is exported by necessity: the
   car host is another process and binds it by intent. `ALLOW_ALL_HOSTS_VALIDATOR`
   turns that into "anything on the phone may drive Jarvis's car surface", and
   it is one line, it compiles, and it works perfectly in a car.
2. **The microphone is not opened by arriving.** A surface that started
   listening because a car connected is a surface nobody agreed to.
3. **Amplitude does not drive the display.** It arrives per audio buffer, and
   a car host throttles template refreshes hard — so one sentence spent that
   way is a screen that stops updating exactly when the reply comes.
4. **The manifest declares what the code implements.** The category, the
   automotive descriptor and the API level are three files that have to agree,
   and disagreeing produces "Jarvis does not appear in the car" with no error
   anywhere.

Run:  python3 android-app/tools/car_app_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"

SERVICE = KOTLIN / "car/JarvisCarAppService.kt"
SCREEN = KOTLIN / "car/JarvisCarScreen.kt"
RENDERER = KOTLIN / "car/CarOrbRenderer.kt"
MANIFEST = ANDROID / "app/src/main/AndroidManifest.xml"
DESCRIPTOR = ANDROID / "app/src/main/res/xml/automotive_app_desc.xml"
BUILD = ANDROID / "app/build.gradle.kts"
CATALOG = ANDROID / "gradle/libs.versions.toml"


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def code_only(path: Path) -> str:
    """The file with its comments removed.

    Because a spec that greps a whole Kotlin file cannot tell code from prose,
    and this one is written about a file whose KDoc explains at length why it
    does NOT use `ALLOW_ALL_HOSTS_VALIDATOR` — which the first version of these
    checks read as using it. Any check phrased as "this identifier must not
    appear" has to look at code, or documenting a hazard becomes the hazard.
    """
    text = source(path)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def check_the_host_is_validated() -> list[str]:
    failures = []
    src = code_only(SERVICE)
    if "ALLOW_ALL_HOSTS_VALIDATOR" in src:
        failures.append(
            "JarvisCarAppService uses ALLOW_ALL_HOSTS_VALIDATOR. The service is "
            "exported because the car host binds it from another process, so this "
            "lets any app on the phone drive the car surface — including in a debug "
            "build, which is an APK somebody is carrying around."
        )
    if "createHostValidator" not in src:
        failures.append(
            "JarvisCarAppService does not override createHostValidator, so it takes "
            "whatever the library defaults to rather than saying what it allows"
        )
    if "hosts_allowlist" not in src:
        failures.append(
            "the host validator checks against no allowlist — the mechanism is "
            "matching a caller's signing digest against a package name, and without "
            "the array there is nothing to match against"
        )
    return failures


def check_arriving_does_not_open_the_microphone() -> list[str]:
    """A car connecting must not start a conversation."""
    failures = []
    src = code_only(SCREEN)

    # The conversation may only be constructed from the explicit control.
    starts = re.findall(r"JarvisConversation\(", src)
    if len(starts) != 1:
        failures.append(
            f"JarvisCarScreen builds a JarvisConversation in {len(starts)} places; "
            "there should be exactly one, reached from the Talk control, so that "
            "'the car connected' can never be a way to open a microphone"
        )
    body = src[src.index("private fun toggleTalking"):] if "toggleTalking" in src else ""
    if "JarvisConversation(" not in body:
        failures.append(
            "the conversation is not started from toggleTalking, so something other "
            "than the driver pressing Talk is starting it"
        )
    for hook in ("onStop", "onDestroy"):
        if f"override fun {hook}(" not in src:
            failures.append(
                f"JarvisCarScreen does not handle {hook}, so a microphone it opened "
                "outlives the screen — and a car that disconnects leaves it running"
            )
    if "stopTalking()" not in src:
        failures.append("nothing stops a conversation this screen started")

    # And it must check the permission rather than assume the car granted it.
    if "RECORD_AUDIO" not in src:
        failures.append(
            "the car screen never checks RECORD_AUDIO. The car host cannot grant it "
            "and must not appear to: without the check, Talk is a button that "
            "silently does nothing on a phone that never granted the microphone."
        )
    return failures


def check_the_refresh_budget_is_respected() -> list[str]:
    """Per-buffer callbacks must not become template pushes."""
    failures = []
    src = code_only(SCREEN)

    amplitude = re.search(
        r"override fun onAmplitude\([^)]*\)[^\n]*\n?(.*?)(?=\n    (?:/\*\*|override|private|public|\}))",
        src,
        re.S,
    )
    if not amplitude:
        failures.append("JarvisCarScreen does not implement onAmplitude")
    elif "invalidate" in amplitude.group(0) or "render()" in amplitude.group(0):
        failures.append(
            "onAmplitude refreshes the car template. It is called per audio buffer — "
            "tens of times a second — and a car host allows a small number of "
            "refreshes per interaction, so one spoken sentence would exhaust the "
            "budget and the reply would never appear."
        )

    # The de-duplication has to be LOAD-BEARING, not merely present.
    #
    # The first version of this check asserted that the field existed, which
    # stayed true when the comparison using it was disabled — the field was
    # still assigned, still read nowhere that mattered, and identical templates
    # went back to being pushed. Found by trying exactly that.
    render = re.search(
        r"private fun render\(\)[^\n]*\{(.*?)\n    \}", src, re.S
    )
    if not render:
        failures.append("JarvisCarScreen has no render() that could de-duplicate anything")
    else:
        body = render.group(1)
        guarded = re.search(r"if\s*\([^)]*lastRendered[^)]*\)\s*return", body)
        if not guarded:
            failures.append(
                "render() does not return early when the template would be identical, "
                "so redraws that change nothing still spend the car's refresh budget — "
                "and the budget is small enough that one spoken sentence exhausts it"
            )
        if "invalidate()" not in body:
            failures.append("render() never actually pushes a template")
    return failures


def check_the_manifest_and_the_code_agree() -> list[str]:
    failures = []
    manifest = source(MANIFEST)

    service = re.search(
        r"<service[^>]*android:name=\"\.car\.JarvisCarAppService\".*?</service>",
        manifest,
        re.S,
    )
    if not service:
        failures.append(
            "JarvisCarAppService is not declared in the manifest, so no car host can "
            "ever find it and Jarvis simply does not appear on the display"
        )
        return failures
    block = service.group(0)
    if "androidx.car.app.CarAppService" not in block:
        failures.append("the car service has no androidx.car.app.CarAppService action")
    if not re.search(r"category android:name=\"androidx\.car\.app\.category\.", block):
        failures.append(
            "the car service declares no category, and the host uses it to decide "
            "which section of the launcher this app belongs in"
        )
    if 'android:exported="true"' not in block:
        failures.append(
            "the car service is not exported, so the host — a different process — "
            "cannot bind it"
        )

    if "androidx.car.app.minCarApiLevel" not in manifest:
        failures.append("no minCarApiLevel is declared; the host refuses to load the app")
    if "com.google.android.gms.car.application" not in manifest:
        failures.append(
            "the automotive descriptor is not referenced from the manifest, so Android "
            "Auto does not know this app has a car surface at all"
        )

    if not DESCRIPTOR.is_file():
        failures.append(f"{DESCRIPTOR.relative_to(ANDROID)} is missing")
    else:
        desc = source(DESCRIPTOR)
        if '<uses name="template"' not in desc:
            failures.append(
                "the automotive descriptor does not declare the template capability, "
                "which is the one this app implements"
            )
        for pretending in ("media", "notification", "navigation"):
            if f'name="{pretending}"' in desc:
                failures.append(
                    f"the automotive descriptor claims the {pretending} capability, "
                    "which nothing in this app implements — the host will offer it to "
                    "the driver and then fail to satisfy it"
                )
    return failures


def check_the_orb_is_the_real_one() -> list[str]:
    """The car shows Jarvis, not a picture of Jarvis."""
    failures = []
    src = code_only(RENDERER)
    if "ReactorOrb(" not in src:
        failures.append(
            "the car surface does not draw with ReactorOrb, so the thing on the head "
            "unit is a different object from the one on the phone and in the browser"
        )
    if "SiriPalette" not in src:
        failures.append(
            "the car orb does not take its colours from SiriPalette, so its states "
            "can drift from every other surface's"
        )
    if "turbulence = false" not in src:
        failures.append(
            "the car orb enables turbulence, which only reads as motion — a still "
            "frame of it is an asymmetric orb rather than a thinking one"
        )
    screen = code_only(SCREEN)
    if "CarOrbRenderer.render" not in screen:
        failures.append("the car screen never draws the orb")
    return failures


def check_the_dependency_is_pinned() -> list[str]:
    failures = []
    catalog = source(CATALOG)
    build = source(BUILD)
    if "androidx.car.app:app" not in catalog:
        failures.append("the car app library is not in the version catalog")
    if re.search(r'carApp = "[^"]*\+', catalog):
        failures.append(
            "the car app library floats on a dynamic version, so the car surface can "
            "start failing on a day nobody changed anything"
        )
    for wanted in ("libs.androidx.car.app", "libs.androidx.car.app.projected"):
        if wanted not in build:
            failures.append(f"{wanted} is not on the app's classpath")
    return failures


def main() -> int:
    for path in (SERVICE, SCREEN, RENDERER, MANIFEST, BUILD, CATALOG):
        if not path.is_file():
            print(f"FAIL  {path} is missing", file=sys.stderr)
            return 1

    failures = (
        check_the_host_is_validated()
        + check_arriving_does_not_open_the_microphone()
        + check_the_refresh_budget_is_respected()
        + check_the_manifest_and_the_code_agree()
        + check_the_orb_is_the_real_one()
        + check_the_dependency_is_pinned()
    )
    for failure in failures:
        print(f"FAIL  {failure}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} failure(s)", file=sys.stderr)
        return 1

    print(
        "car app: the host is validated, arriving opens no microphone, amplitude "
        "does not spend the refresh budget, and the manifest declares what the code "
        "implements"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
