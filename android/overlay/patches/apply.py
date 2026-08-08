#!/usr/bin/env python3
"""Apply the Jarvis overlay patches to a home-assistant/android fork.

Idempotent: every edit is guarded by a JARVIS-PATCH marker or an existence
check, so re-running is always safe. Preferred over unified diffs because the
upstream repo churns constantly and diffs would rot within weeks; these edits
anchor on stable structural landmarks instead of exact line content.

What it does (all inside the fork checkout):
  1. build.gradle.kts: registers a "jarvis" product flavor
     (dimension "version", applicationIdSuffix ".jarvis",
     versionNameSuffix "-jarvis") appended as a top-level statement -- Gradle
     Kotlin DSL executes it during configuration, before variants are
     computed, so appending is as good as editing the productFlavors block
     and far more robust than brace-counting regexes.
     NOTE: upstream now declares the full/minimal flavors in a convention
     plugin (`alias(libs.plugins.homeassistant.android.flavor)` ->
     AndroidFullMinimalFlavorConventionPlugin, dimension "version"), not
     inline in app/build.gradle.kts. The appended statement still works
     because the convention plugin has already created the "version" dimension
     by the time this runs; we just detect either arrangement.
  2. build.gradle.kts: points the jarvis source set at src/minimal/* in
     addition to src/jarvis/* -- the jarvis flavor EXTENDS "minimal" (no
     Google Play Services), which is what a degoogled GrapheneOS device
     needs. The flavor-specific classes (push provider, location sensor
     managers, ...) come from the minimal source set.
  3. build.gradle.kts: mirrors every `"minimalImplementation"(...)`
     dependency line as `"jarvisImplementation"(...)`.
  4. (removed) The overlay used to forward to HA's own AssistActivity via an
     injected newJarvisIntent() helper. The jarvis flavor is now a
     SELF-CONTAINED assist client (mic -> HA WebSocket pipeline -> TTS, driven
     entirely by JarvisAssistActivity), so it no longer patches or depends on
     AssistActivity at all. patch_assist_activity() is kept for reference but
     is not run.
  5. app/google-services.json: writes a mock file if none exists, so tooling
     that expects it (google-services plugin applied when the file is
     present) does not fail. The jarvis flavor itself never uses GMS.

Usage:
    python3 apply.py /path/to/ha-android-fork
    HA_ANDROID_DIR=/path/to/fork python3 apply.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

MARKER_BEGIN = "// JARVIS-PATCH-BEGIN"
MARKER_END = "// JARVIS-PATCH-END"

# Flavor dimension the full/minimal flavors live on. Upstream uses "version"
# (AndroidFullMinimalFlavorConventionPlugin: flavorDimensions.add("version")).
# Override with JARVIS_FLAVOR_DIMENSION if that ever changes.
FLAVOR_DIMENSION = os.environ.get("JARVIS_FLAVOR_DIMENSION", "version")

FLAVOR_BLOCK = f"""
// JARVIS-PATCH-BEGIN flavor (added by jarvis overlay/patches/apply.py; do not edit)
// The jarvis flavor extends "minimal": no Google Play Services, suitable for
// GrapheneOS. Sources come from src/jarvis/ plus the minimal flavor's dirs.
// Appended as a top-level statement: the flavor convention plugin has already
// created the "{FLAVOR_DIMENSION}" dimension (and full/minimal) by the time
// this runs, so adding another flavor here is valid.
android.productFlavors.create("jarvis") {{
    dimension = "{FLAVOR_DIMENSION}"
    applicationIdSuffix = ".jarvis"
    versionNameSuffix = "-jarvis"
}}
android.sourceSets.getByName("jarvis") {{
    java.srcDirs("src/minimal/java", "src/minimal/kotlin", "src/jarvis/java", "src/jarvis/kotlin")
    res.srcDirs("src/minimal/res", "src/jarvis/res")
}}
// JARVIS-PATCH-END flavor
"""

ASSIST_HELPER = """\
        // JARVIS-PATCH-BEGIN newJarvisIntent (added by jarvis overlay/patches/apply.py)
        // Public, stable entry point for the jarvis flavor's activation
        // activity. Keeps the overlay off AssistActivity's private extras.
        fun newJarvisIntent(context: android.content.Context): android.content.Intent =
            newInstance(context, startListening = true, fromFrontend = false)
        // JARVIS-PATCH-END newJarvisIntent
"""


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def info(msg: str) -> None:
    print(f"  [apply.py] {msg}")


def find_fork_root() -> Path:
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    elif os.environ.get("HA_ANDROID_DIR"):
        root = Path(os.environ["HA_ANDROID_DIR"])
    else:
        root = Path.cwd()
    root = root.resolve()
    gradle = root / "app" / "build.gradle.kts"
    if not gradle.is_file():
        fail(
            f"{gradle} not found. Pass the fork checkout as the first argument "
            "or set HA_ANDROID_DIR. Expected a home-assistant/android checkout "
            "with app/build.gradle.kts (Gradle Kotlin DSL)."
        )
    return root


def patch_gradle_flavor(root: Path) -> None:
    gradle = root / "app" / "build.gradle.kts"
    text = gradle.read_text(encoding="utf-8")

    if "JARVIS-PATCH-BEGIN flavor" in text:
        info("build.gradle.kts: jarvis flavor block already present, skipping.")
        return
    if re.search(r'create\(\s*"jarvis"\s*\)', text):
        info("build.gradle.kts: create(\"jarvis\") already exists (manual edit?), skipping.")
        return

    # The full/minimal flavors (dimension "version") may be declared either
    # inline in this file, OR by the flavor convention plugin applied in the
    # plugins {} block. Accept either; the appended block only needs the
    # "version" dimension to exist by configuration time, which both provide.
    inline = re.search(r'create\(\s*"minimal"\s*\)', text)
    via_plugin = (
        "homeassistant.android.flavor" in text
        or re.search(r'plugins\.[\w.]*flavor', text) is not None
    )
    if not (inline or via_plugin):
        # Last resort: look for the convention plugin in build-logic.
        blogic = root / "build-logic"
        found = list(blogic.rglob("*FlavorConventionPlugin.kt")) if blogic.is_dir() else []
        if not found:
            fail(
                "Could not find the full/minimal flavors: neither an inline "
                'create("minimal") in app/build.gradle.kts nor the flavor '
                "convention plugin (alias(libs.plugins.homeassistant.android."
                "flavor) / *FlavorConventionPlugin.kt). Upstream flavor setup "
                "changed - inspect the fork and update apply.py."
            )
        info(f"detected flavor convention plugin: {found[0].relative_to(root)}")
    else:
        info(
            "flavors declared "
            + ("inline in app/build.gradle.kts." if inline
               else "via the flavor convention plugin.")
        )

    gradle.write_text(text.rstrip("\n") + "\n" + FLAVOR_BLOCK, encoding="utf-8")
    info("build.gradle.kts: appended jarvis flavor + source-set block "
         f'(dimension "{FLAVOR_DIMENSION}").')


def patch_gradle_deps(root: Path) -> None:
    gradle = root / "app" / "build.gradle.kts"
    text = gradle.read_text(encoding="utf-8")

    if "JARVIS-PATCH-BEGIN deps" in text:
        info("build.gradle.kts: jarvis deps block already present, skipping.")
        return

    minimal_deps = re.findall(r'^\s*"minimalImplementation"\((.+)\)\s*$', text, re.MULTILINE)
    if not minimal_deps:
        info(
            "build.gradle.kts: no \"minimalImplementation\"(...) lines found - "
            "nothing to mirror. (Fine if the minimal flavor has no extra deps.)"
        )
        return

    lines = "\n".join(f'    "jarvisImplementation"({dep})' for dep in minimal_deps)
    block = (
        "\n// JARVIS-PATCH-BEGIN deps (mirrored from minimalImplementation by apply.py)\n"
        "dependencies {\n"
        f"{lines}\n"
        "}\n"
        "// JARVIS-PATCH-END deps\n"
    )
    gradle.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
    info(f"build.gradle.kts: mirrored {len(minimal_deps)} minimalImplementation dep(s) to jarvisImplementation.")


def patch_assist_activity(root: Path) -> None:
    candidates = sorted((root / "app" / "src" / "main").rglob("AssistActivity.kt"))
    if not candidates:
        fail(
            "AssistActivity.kt not found under app/src/main/. The overlay's "
            "JarvisAssistActivity forwards to it and needs the newJarvisIntent() "
            "helper. Locate the assist activity in the fork and update "
            "patch_assist_activity() in apply.py."
        )
    if len(candidates) > 1:
        info(f"multiple AssistActivity.kt found, using first: {candidates[0]}")
    path = candidates[0]
    text = path.read_text(encoding="utf-8")

    if "JARVIS-PATCH-BEGIN newJarvisIntent" in text:
        info(f"{path.relative_to(root)}: newJarvisIntent already present, skipping.")
        return

    m = re.search(r"^(\s*)companion object\s*\{\s*$", text, re.MULTILINE)
    if not m:
        fail(
            f"{path}: could not find a 'companion object {{' line to anchor the "
            "newJarvisIntent helper. Add it manually:\n" + ASSIST_HELPER
        )
    insert_at = m.end()
    text = text[:insert_at] + "\n" + ASSIST_HELPER + text[insert_at:]
    path.write_text(text, encoding="utf-8")
    info(f"{path.relative_to(root)}: inserted newJarvisIntent() helper into companion object.")

    if "fun newInstance(" not in text:
        info(
            "WARNING: AssistActivity.kt has no newInstance(...) - the inserted "
            "helper will not compile. Adjust the helper to whatever intent "
            "factory upstream now provides."
        )


def write_mock_google_services(root: Path) -> None:
    target = root / "app" / "google-services.json"
    if target.exists():
        info("app/google-services.json already exists, leaving it alone.")
        return

    base = "io.homeassistant.companion.android"
    packages = []
    for suffix in ("", ".minimal", ".jarvis"):
        for dbg in ("", ".debug"):
            packages.append(base + suffix + dbg)

    mock = {
        "project_info": {
            "project_number": "000000000000",
            "project_id": "jarvis-mock",
            "storage_bucket": "jarvis-mock.appspot.com",
        },
        "client": [
            {
                "client_info": {
                    "mobilesdk_app_id": "1:000000000000:android:0000000000000000",
                    "android_client_info": {"package_name": pkg},
                },
                "oauth_client": [],
                "api_key": [{"current_key": "AIzaSyMockMockMockMockMockMockMockMock0"}],
                "services": {"appinvite_service": {"other_platform_oauth_client": []}},
            }
            for pkg in packages
        ],
        "configuration_version": "1",
    }
    target.write_text(json.dumps(mock, indent=2) + "\n", encoding="utf-8")
    info(
        "app/google-services.json: wrote MOCK file (build tooling guard only - "
        "the jarvis flavor never talks to Google; FCM push will not work in "
        "any flavor built with this mock, which is expected)."
    )


def check_overlay_copied(root: Path) -> None:
    probe = root / "app" / "src" / "jarvis" / "AndroidManifest.xml"
    if not probe.is_file():
        info(
            "NOTE: app/src/jarvis/ overlay sources not present yet. "
            "Run android/apply-to-fork.sh, or copy overlay/app/src/jarvis "
            "into the fork's app/src/ yourself."
        )


def main() -> None:
    root = find_fork_root()
    print(f"Applying jarvis overlay patches to: {root}")
    patch_gradle_flavor(root)
    patch_gradle_deps(root)
    # patch_assist_activity(root)  # no longer needed: overlay is self-contained
    write_mock_google_services(root)
    check_overlay_copied(root)
    print("Done. Build with: ./gradlew :app:assembleJarvisRelease")


if __name__ == "__main__":
    main()
