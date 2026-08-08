#!/usr/bin/env python3
"""Apply the Jarvis overlay to a home-assistant/android fork.

Idempotent: every edit is guarded by a JARVIS-PATCH marker or an existence
check, so re-running is always safe.

Design (see docs/android.md): rather than adding a new product flavor that
would have to re-inherit the `minimal` flavor's sources, dependencies and
BuildConfig wiring (a brittle cascade), the Jarvis code lives in the **main**
source set and we build the existing, degoogled **minimal** flavor. So this
patcher only has to:

  1. app/src/main/AndroidManifest.xml: merge in Jarvis permissions +
     components (activation activity, settings activity, voice-interaction
     service + session service, stub recognition service), guarded by markers.
  2. app/google-services.json: write a mock if none exists, so tooling keyed
     off the file's presence doesn't fail (the minimal flavor never uses GMS).

The Kotlin sources (app/src/main/kotlin/.../jarvis/**) and resources
(res/values/jarvis_styles.xml, res/xml/jarvis_voice_interaction_service.xml)
are copied into app/src/main by android/apply-to-fork.sh before this runs.

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

MARKER_BEGIN = "<!-- JARVIS-PATCH-BEGIN (added by overlay/patches/apply.py) -->"
MARKER_END = "<!-- JARVIS-PATCH-END -->"

PERMISSIONS = (
    "android.permission.RECORD_AUDIO",
    "android.permission.INTERNET",
)

# Inserted just before </application>. Component names are relative (".jarvis.*")
# so they resolve against the app namespace (io.homeassistant.companion.android).
COMPONENTS_XML = """\
        <!-- Jarvis home / launcher: the app opens into the Jarvis HUD. -->
        <activity
            android:name=".jarvis.JarvisHomeActivity"
            android:exported="true"
            android:label="Jarvis"
            android:theme="@style/Theme.JarvisHome"
            android:launchMode="singleTask"
            android:configChanges="orientation|screenSize|screenLayout|keyboardHidden|uiMode">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
                <category android:name="android.intent.category.LEANBACK_LAUNCHER" />
            </intent-filter>
        </activity>

        <!-- Siri-like activation surface + full in-orb conversation. -->
        <activity
            android:name=".jarvis.JarvisAssistActivity"
            android:exported="true"
            android:theme="@style/Theme.JarvisTransparent"
            android:excludeFromRecents="true"
            android:showWhenLocked="true"
            android:turnScreenOn="true"
            android:launchMode="singleTask"
            android:taskAffinity="io.homeassistant.companion.android.jarvis.assist"
            android:noHistory="true"
            android:configChanges="orientation|screenSize|screenLayout|keyboardHidden|uiMode">
            <intent-filter>
                <action android:name="android.intent.action.ASSIST" />
                <action android:name="android.intent.action.VOICE_COMMAND" />
                <category android:name="android.intent.category.DEFAULT" />
            </intent-filter>
        </activity>

        <!-- Connection settings (HA URL + long-lived token + pipeline). -->
        <activity
            android:name=".jarvis.JarvisSettingsActivity"
            android:exported="true"
            android:label="Jarvis settings"
            android:excludeFromRecents="true" />

        <!-- Device-assistant role (Digital assistant app / GrapheneOS). -->
        <service
            android:name=".jarvis.JarvisVoiceInteractionService"
            android:exported="true"
            android:permission="android.permission.BIND_VOICE_INTERACTION"
            android:label="Jarvis">
            <intent-filter>
                <action android:name="android.service.voice.VoiceInteractionService" />
            </intent-filter>
            <meta-data
                android:name="android.voice_interaction"
                android:resource="@xml/jarvis_voice_interaction_service" />
        </service>

        <service
            android:name=".jarvis.JarvisVoiceInteractionSessionService"
            android:exported="true"
            android:permission="android.permission.BIND_VOICE_INTERACTION" />

        <!-- Stub recognizer so android:recognitionService resolves. -->
        <service
            android:name=".jarvis.JarvisRecognitionService"
            android:exported="true"
            android:label="Jarvis (stub recognizer)">
            <intent-filter>
                <action android:name="android.speech.RecognitionService" />
                <category android:name="android.intent.category.DEFAULT" />
            </intent-filter>
        </service>
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
            "or set HA_ANDROID_DIR. Expected a home-assistant/android checkout."
        )
    return root


def patch_main_manifest(root: Path) -> None:
    manifest = root / "app" / "src" / "main" / "AndroidManifest.xml"
    if not manifest.is_file():
        fail(f"{manifest} not found - not a home-assistant/android checkout?")
    text = manifest.read_text(encoding="utf-8")

    if MARKER_BEGIN in text:
        info("AndroidManifest.xml: Jarvis components already present, skipping.")
        return

    # 1. Permissions right after the opening <manifest ...> tag.
    m = re.search(r"<manifest\b[^>]*>", text)
    if not m:
        fail("AndroidManifest.xml: no <manifest> opening tag found.")
    perms = []
    for p in PERMISSIONS:
        if f'"{p}"' not in text:
            perms.append(f'    <uses-permission android:name="{p}" />')
    if perms:
        block = "\n" + MARKER_BEGIN + "\n" + "\n".join(perms) + "\n" + MARKER_END + "\n"
        insert_at = m.end()
        text = text[:insert_at] + block + text[insert_at:]

    # 2. Components right before </application>.
    close = text.rfind("</application>")
    if close == -1:
        fail("AndroidManifest.xml: no </application> tag found.")
    comp_block = (
        "\n        " + MARKER_BEGIN + "\n"
        + COMPONENTS_XML
        + "        " + MARKER_END + "\n"
    )
    text = text[:close] + comp_block + text[close:]

    # 3. Demote Home Assistant's LaunchActivity so Jarvis is the sole launcher
    #    (strip its LAUNCHER / LEANBACK_LAUNCHER categories). It stays startable
    #    explicitly for the Dashboard button.
    text, demoted = _demote_launcher(text)

    # 4. Rebrand the app: name -> Jarvis, icon -> the Jarvis reactor.
    text, rebranded = _rebrand_application(text)

    manifest.write_text(text, encoding="utf-8")
    info(
        "AndroidManifest.xml: merged Jarvis permissions + components "
        f"({len(perms)} permission(s) added); "
        f"launcher demoted={demoted}; rebranded={rebranded}."
    )


def _demote_launcher(text: str) -> tuple[str, bool]:
    m = re.search(
        r'(<activity\b[^>]*android:name="io\.homeassistant\.companion\.android'
        r'\.launch\.LaunchActivity".*?</activity>)',
        text, re.S,
    )
    if not m:
        info("WARN: LaunchActivity not found; you may see two launcher icons.")
        return text, False
    block = m.group(1)
    new_block = re.sub(
        r'[ \t]*<category\s+android:name="android\.intent\.category\.'
        r'(LAUNCHER|LEANBACK_LAUNCHER)"\s*/>\n?',
        "", block,
    )
    if new_block == block:
        return text, False
    return text[: m.start(1)] + new_block + text[m.end(1):], True


def _rebrand_application(text: str) -> bool:
    m = re.search(r"<application\b[^>]*>", text)
    if not m:
        return text, False
    tag = m.group(0)
    new = tag
    new = re.sub(r'android:label="[^"]*"', 'android:label="Jarvis"', new, count=1)
    new = re.sub(r'android:icon="[^"]*"', 'android:icon="@mipmap/ic_jarvis"', new, count=1)
    new = re.sub(
        r'android:roundIcon="[^"]*"',
        'android:roundIcon="@mipmap/ic_jarvis"', new, count=1,
    )
    if new == tag:
        return text, False
    return text[: m.start()] + new + text[m.end():], True


def write_mock_google_services(root: Path) -> None:
    target = root / "app" / "google-services.json"
    if target.exists():
        info("app/google-services.json already exists, leaving it alone.")
        return
    base = "io.homeassistant.companion.android"
    packages = []
    for suffix in ("", ".minimal", ".full"):
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
    info("app/google-services.json: wrote MOCK file (tooling guard only).")


def check_overlay_copied(root: Path) -> None:
    probe = (
        root / "app" / "src" / "main" / "kotlin" / "io" / "homeassistant"
        / "companion" / "android" / "jarvis" / "JarvisAssistActivity.kt"
    )
    if not probe.is_file():
        info(
            "NOTE: Jarvis sources not found under app/src/main/kotlin/.../jarvis. "
            "Run android/apply-to-fork.sh, which copies overlay/app/src/main "
            "into the fork before running this patcher."
        )


def main() -> None:
    root = find_fork_root()
    print(f"Applying jarvis overlay patches to: {root}")
    patch_main_manifest(root)
    write_mock_google_services(root)
    check_overlay_copied(root)
    print("Done. Build with: ./gradlew :app:assembleMinimalDebug")


if __name__ == "__main__":
    main()
