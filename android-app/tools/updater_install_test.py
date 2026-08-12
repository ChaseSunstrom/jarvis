#!/usr/bin/env python3
"""Executable spec: the in-app updater has to actually install something.

`release_feed_test.py` covers which release is worth offering. This covers what
happens after that decision, which is where the updater was broken.

`PackageInstaller.Session.commit()` **shows nothing**. It sends a status to the
`IntentSender` it was handed, and the first status for an ordinary sideloaded
update is `STATUS_PENDING_USER_ACTION`, carrying the system's "do you want to
install this update?" activity in `Intent.EXTRA_INTENT`. Somebody has to start
that activity.

Nobody did. `UpdateChecker` committed to a broadcast of
`ai.jarvis.app.INSTALL_RESULT` for which there was no receiver anywhere in the
app, then returned "offered" and let Settings print *"Ready to install — confirm
the system prompt."* There was no system prompt, on any device, ever. The APK
downloaded, the session committed, the status went nowhere.

The same shape as `speech_host_test.py`'s bug and `runtime_permissions_test.py`'s:
a seam that is written, documented, and never connected to the thing that makes
it do something. This file exists because the failure is silent by
construction — every line of code involved works.

Run:  python3 android-app/tools/updater_install_test.py
      python3 -m pytest android-app/tools/updater_install_test.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"

MANIFEST = ANDROID / "app/src/main/AndroidManifest.xml"
CHECKER = KOTLIN / "update/UpdateChecker.kt"
RECEIVER = KOTLIN / "update/InstallResultReceiver.kt"
SETTINGS = KOTLIN / "SettingsActivity.kt"

ACTION = "ai.jarvis.app.INSTALL_RESULT"


def read(path: Path) -> str:
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def code_only(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"//[^\n]*", " ", source)


# --- the seam has a receiver ------------------------------------------------
def test_something_receives_the_install_status():
    """The bug, stated as a check. A commit whose status goes nowhere is a
    download that installs nothing."""
    assert RECEIVER.is_file(), (
        "nothing receives the installer's status, so STATUS_PENDING_USER_ACTION "
        "is dropped and the system's install prompt is never started"
    )
    src = code_only(read(RECEIVER))
    assert "BroadcastReceiver" in src


def test_the_receiver_is_declared_and_not_exported():
    manifest = read(MANIFEST)
    block = re.search(
        r'<receiver\s+android:name="\.update\.InstallResultReceiver".*?</receiver>',
        manifest,
        re.S,
    )
    assert block, (
        "InstallResultReceiver is not in the manifest. A receiver registered in "
        "code would be gone by the time the install finishes — installing over "
        "ourselves kills this process."
    )
    assert 'android:exported="false"' in block.group(0), (
        "an exported install-result receiver lets any app fabricate a verdict"
    )


def test_the_pending_user_action_actually_starts_the_prompt():
    """The whole point. Logging the status and stopping there is the bug with a
    log line added."""
    src = code_only(read(RECEIVER))
    assert "STATUS_PENDING_USER_ACTION" in src, "the receiver ignores the one status that matters"
    body = src.split("STATUS_PENDING_USER_ACTION", 1)[1][:1400]
    assert "Intent.EXTRA_INTENT" in body, (
        "the receiver does not read the activity the installer handed it"
    )
    assert "startActivity(" in body, "the install prompt is never started"
    assert "FLAG_ACTIVITY_NEW_TASK" in body, (
        "a BroadcastReceiver has no task; starting an activity without NEW_TASK "
        "throws and the prompt is lost again"
    )


def test_a_refused_background_start_leaves_something_to_tap():
    """Android 10+ refuses a background activity start, and this receiver fires
    exactly when the user has wandered off. A silent refusal here is
    indistinguishable from the original bug."""
    src = code_only(read(RECEIVER))
    assert "catch" in src.split("startActivity(", 1)[1][:400], (
        "the background-start refusal is not caught"
    )
    assert "notifyWith(" in src and "PendingIntent.getActivity(" in src, (
        "there is no notification carrying the install prompt"
    )
    assert "FLAG_MUTABLE" in src, (
        "the installer's intent is the system's own and must not be frozen; an "
        "immutable PendingIntent around it cannot complete the install"
    )


def test_failures_reach_the_user_rather_than_only_logcat():
    src = code_only(read(RECEIVER))
    assert "EXTRA_STATUS_MESSAGE" in src
    assert "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in src, (
        "the one failure with a specific remedy — a build signed with another "
        "key — is not explained"
    )


# --- the checker's half ------------------------------------------------------
def test_the_commit_addresses_the_receiver_explicitly():
    """An action + package broadcast works, but an explicit one cannot be
    caught by the Android 8 implicit-broadcast restrictions and cannot be
    intercepted."""
    src = code_only(read(CHECKER))
    body = src.split("private fun confirmationIntent()", 1)
    assert len(body) == 2, "confirmationIntent is gone"
    body = body[1][:700]
    assert "InstallResultReceiver::class.java" in body, (
        "the install status is still broadcast by action alone, so nothing is "
        "guaranteed to receive it"
    )
    assert "FLAG_MUTABLE" in body, "the installer cannot fill in its own extras"


def test_the_install_permission_is_checked_before_the_download():
    """`REQUEST_INSTALL_PACKAGES` is in the manifest and has been a per-app user
    grant since Android 8 — the same declared-but-not-held shape as the nine
    permissions in `runtime_permissions_test.py`. Without it the commit succeeds
    and the prompt is refused: sixty megabytes to arrive at a silence."""
    src = code_only(read(CHECKER))
    assert "canRequestPackageInstalls()" in src, (
        "nothing checks whether Android will let Jarvis install anything"
    )
    install = src.split("fun install(", 1)
    assert len(install) == 2, "UpdateChecker.install is gone"
    body = install[1]
    check_at = body.index("canInstallPackages()")
    download_at = body.index("createSession(")
    assert check_at < download_at, (
        "the permission is checked after the download has already started"
    )


def test_no_failure_path_leaks_an_install_session():
    """`use` is inline, so a `return` inside one is a NON-LOCAL return.

    `install()` has two of them — HTTP not-200, and a response with no body —
    sitting inside `openSession(...).use { ... execute().use { ... } }`. They
    unwind the sessions and close the streams, and they do not enter the
    `catch`. The `catch` was the only caller of `abandonSession`, so the two
    most ordinary download failures each left a created, half-written session
    behind, silently.

    PackageInstaller caps ACTIVE sessions per app (50 on current Android) and a
    session that is never abandoned stays active for days. Around fifty taps of
    CHECK FOR UPDATES on a flaky network and `createSession` throws
    IllegalStateException("Too many active sessions") — the updater wedged for
    days by a failure with nothing to do with the real problem, which is the
    exact outcome the comment in that catch promised to prevent.

    So the release moves to a `finally`, and the check is that it stays there.
    """
    src = code_only(read(CHECKER))
    body = src.split("fun install(", 1)
    assert len(body) == 2, "UpdateChecker.install is gone"
    body = body[1]
    assert "abandonSession(" in body, "a failed install no longer releases its session"
    finally_at = body.find("} finally {")
    assert finally_at >= 0, (
        "install() has no finally block, so the early returns inside its `use` "
        "blocks can bypass whatever releases the session"
    )
    assert body.index("abandonSession(") > finally_at, (
        "the session is released outside the finally, so a non-local return "
        "from one of the nested `use` blocks walks past it"
    )
    # ...and only when there is something to release. Abandoning a COMMITTED
    # session would cancel the install this method exists to start.
    assert "committed = true" in body and "!committed" in body, (
        "the finally cannot tell a committed session from an abandoned one, so "
        "it either leaks or cancels the install it just asked for"
    )


def test_committed_is_not_the_same_word_as_found():
    """`check()` returning `Offered` and `install()` returning `Offered` shared
    a name and meant different things, which is how "Ready to install — confirm
    the system prompt" came to be printed by a build where no prompt existed."""
    src = code_only(read(CHECKER))
    assert "data class Handed(" in src, (
        "install() still reports the same result as check(), so the UI cannot "
        "tell 'an update exists' from 'the installer has it'"
    )
    install = src.split("fun install(", 1)[1]
    assert "return Result.Handed(update)" in install
    assert "return Result.Offered(update)" not in install, (
        "install() still claims to have merely found the update"
    )


def test_the_status_line_does_not_promise_a_prompt_it_cannot_raise():
    src = code_only(read(SETTINGS))
    described = src.split("private fun describe(", 1)
    assert len(described) == 2, "SettingsActivity.describe is gone"
    body = described[1][:1400]
    assert "Result.Handed" in body, "the new state has no sentence"
    assert "notifications" in body, (
        "the status line does not mention the notification fallback, so a "
        "refused background start reads as nothing happening"
    )


def test_the_grant_is_one_tap_from_the_update_button():
    """A failure that names a Settings screen and does not open it is a failure
    the user has to go and look for."""
    src = code_only(read(SETTINGS))
    assert "openInstallPermission()" in src, (
        "Settings offers no way to grant 'install unknown apps'"
    )
    assert "ACTION_MANAGE_UNKNOWN_APP_SOURCES" in src
    body = src.split("private fun openInstallPermission()", 1)
    assert len(body) == 2, "the button calls something that does not exist"
    assert 'Uri.parse("package:$packageName")' in body[1][:900], (
        "without a package: URI this lands on the full app list rather than on "
        "Jarvis's own switch"
    )


def test_the_action_string_is_still_the_one_the_manifest_filters_for():
    assert ACTION in read(CHECKER)
    assert ACTION in read(MANIFEST)


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
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
