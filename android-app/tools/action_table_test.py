#!/usr/bin/env python3
"""The local action table, checked against the brief and against its own docs.

`Builtins.kt` and the `builtin/` package ARE the authority on how dangerous
each action is — the tier field on the wire can only raise what is written
there. That makes an action declared at the wrong tier a security bug that no
amount of policy-engine correctness can catch, so the tiers the shared brief
names explicitly are pinned here, in a file that runs.

Also checked, because each has already gone wrong once:

  * no duplicate ids (a duplicate throws at registration and takes the whole
    registry with it);
  * every action is actually registered in `Builtins.all()` — an action object
    nobody adds is dead code that still looks reviewed;
  * every action that returns `markUntrusted()` content also declares
    `untrustedOutput = true`, which is the flag the task runner needs to taint
    a variable. `markUntrusted()` alone only tells the SERVER; the flag is what
    keeps web/screen/file text from driving a later step locally;
  * `docs/actions.md` still states the tier the code enforces.

Run:  python3 android-app/tools/action_table_test.py
      python3 -m pytest android-app/tools/action_table_test.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILTIN = ROOT / "app/src/main/kotlin/ai/jarvis/app/automation/actions/builtin"
DELEGATE = ROOT / (
    "app/src/main/kotlin/ai/jarvis/app/automation/actions/UiAutomationDelegate.kt"
)
DOCS = ROOT / "docs/actions.md"

# From the shared brief. The left column is the id, the right is the LOWEST
# tier it may carry. Anything stricter is fine; anything looser is a bug.
REQUIRED_TIER = {
    # Tier 3 — "every single time"
    "send_sms": "CONFIRM",
    "place_call": "CONFIRM",
    "dial": "CONFIRM",
    "ui_click": "CONFIRM",
    "ui_type": "CONFIRM",
    "run_shell": "CONFIRM",
    "delete_file": "CONFIRM",
    "kill_app": "CONFIRM",
    # The last Tasker rows (M61). A camera must never fire quietly, the inbox
    # and the call log are other people's words, and hanging up is done to a
    # person — each is pinned here because none lives in CommsActions.kt, so
    # the file-name check below would not see one of them slipping to Tier 2.
    "take_photo": "CONFIRM",
    "read_sms": "CONFIRM",
    "read_call_log": "CONFIRM",
    "end_call": "CONFIRM",
    # Tier 2 — "changes device state but is recoverable"
    "set_alarm": "NOTIFY",
    "set_timer": "NOTIFY",
    "create_calendar_event": "NOTIFY",
    "write_file": "NOTIFY",
    "read_clipboard": "NOTIFY",
    "write_clipboard": "NOTIFY",
    # A tag is held to the phone by hand, but what is written replaces what
    # the tag held: asked about at least once.
    "nfc_write": "NOTIFY",
    "set_ringer_mode": "NOTIFY",
    "toggle_dnd": "NOTIFY",
    "take_screenshot": "NOTIFY",
    "start_navigation": "NOTIFY",
    "share_text": "NOTIFY",
    "set_brightness": "NOTIFY",
    "read_contacts": "NOTIFY",
    "http_request": "NOTIFY",
    "ui_read_screen": "NOTIFY",
}

# Tier 1 by name in the brief: these must NOT have been quietly promoted past
# AUTO either, or the phone nags about reading its own battery.
MUST_BE_AUTO = {
    "get_device_state",
    "set_volume",
    "set_media_volume",
    "media_play",
    "media_pause",
    "media_next",
    "media_previous",
    "media_stop",
    "toggle_torch",
    "launch_app",
    "send_notification",
    "read_calendar",
    "list_files",
    "read_file",
}

TIER_ORDER = ["AUTO", "NOTIFY", "CONFIRM"]


# --- parsing ----------------------------------------------------------------


def _delegated_ids() -> dict[str, str]:
    src = DELEGATE.read_text()
    return dict(re.findall(r'const val (\w+)\s*=\s*"([^"]+)"', src))


def parse_actions() -> dict[str, dict]:
    """id -> {tier, file, owner, untrusted_output, marks_untrusted}."""
    consts = _delegated_ids()
    out: dict[str, dict] = {}
    duplicates: list[str] = []

    for path in sorted(BUILTIN.glob("*.kt")):
        src = path.read_text()

        # `object Foo : JarvisAction { override val id = "x" ... }` and the
        # `DelegatedUiAction(...)` / `MediaKeyAction(...)` factory forms.
        blocks: list[tuple[str, str, str]] = []  # (owner, body, kind)
        starts = [
            (m.start(), m.group(1))
            for m in re.finditer(r"\n(?:object|internal object) (\w+) : JarvisAction", src)
        ]
        for i, (pos, name) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(src)
            blocks.append((name, src[pos:end], "object"))

        for name, body, _kind in blocks:
            m = re.search(r'override val id\s*=\s*"([^"]+)"', body)
            if not m:
                continue
            record(out, duplicates, m.group(1), body, path.name, name)

        # DelegatedUiAction(id = UiAutomationDelegate.UI_CLICK, tier = ..., ...)
        for m in re.finditer(
            r"DelegatedUiAction\((.*?)\n    \)", src, re.S
        ):
            args = m.group(1)
            cm = re.search(r"id\s*=\s*UiAutomationDelegate\.(\w+)", args)
            if not cm:
                continue
            record(out, duplicates, consts[cm.group(1)], args, path.name, "DelegatedUiAction")

        # MediaKeyAction("media_play", "…", KEYCODE) { … }
        for m in re.finditer(r'MediaKeyAction\(\s*\n?\s*"([^"]+)"', src):
            record(out, duplicates, m.group(1), "override val tier = ActionTier.AUTO",
                   path.name, "MediaKeyAction")

    assert not duplicates, f"duplicate action ids (registration would throw): {duplicates}"
    return out


def record(out, duplicates, action_id, body, filename, owner):
    if action_id in out:
        duplicates.append(action_id)
        return
    tier = re.search(r"tier\s*=\s*ActionTier\.(\w+)", body)
    out[action_id] = {
        "tier": tier.group(1) if tier else None,
        "file": filename,
        "owner": owner,
        "untrusted_output": bool(
            re.search(r"untrustedOutput\s*=\s*true", body)
        ),
        "marks_untrusted": "markUntrusted()" in body,
    }


ACTIONS = parse_actions()


# --- tests ------------------------------------------------------------------


def test_every_action_declares_a_tier():
    missing = [k for k, v in ACTIONS.items() if v["tier"] not in TIER_ORDER]
    assert not missing, f"actions with no parseable tier: {missing}"


def test_the_briefs_tier3_actions_are_tier3():
    for action_id, floor in REQUIRED_TIER.items():
        assert action_id in ACTIONS, f"{action_id} has disappeared from the table"
        actual = ACTIONS[action_id]["tier"]
        assert TIER_ORDER.index(actual) >= TIER_ORDER.index(floor), (
            f"{action_id} is {actual}, the brief requires at least {floor}"
        )


def test_the_briefs_tier1_actions_did_not_get_promoted():
    for action_id in MUST_BE_AUTO:
        assert action_id in ACTIONS, f"{action_id} has disappeared from the table"
        assert ACTIONS[action_id]["tier"] == "AUTO", (
            f"{action_id} is {ACTIONS[action_id]['tier']}, the brief calls it Tier 1"
        )


def test_nothing_that_reaches_another_person_is_below_tier3():
    """Belt and braces: catch a NEW action in the comms/shell files too."""
    for action_id, info in ACTIONS.items():
        if info["file"] in ("CommsActions.kt", "ShellActions.kt"):
            if action_id in ("read_contacts", "send_notification"):
                continue  # local-only, tiered on their own merits
            assert info["tier"] == "CONFIRM", (
                f"{action_id} in {info['file']} is {info['tier']}, not CONFIRM"
            )


def test_content_returning_actions_declare_untrusted_output():
    """`markUntrusted()` tells the server; `untrustedOutput` tells the phone.

    Only the second one can stop a page's text from feeding a later step, so an
    action that has one must have the other.
    """
    liars = [
        k for k, v in ACTIONS.items()
        if v["marks_untrusted"] and not v["untrusted_output"]
    ]
    assert not liars, (
        f"these return untrusted content but do not declare untrustedOutput: {liars}"
    )


def test_the_known_content_sources_are_all_flagged():
    expected = {
        "http_request", "read_file", "read_clipboard", "read_contacts",
        "read_calendar", "run_shell", "list_installed_apps",
        "ui_read_screen", "ui_wait_for", "take_screenshot",
        # M61: a message body, a cached caller name, a barcode's text and a
        # tag's records are all written by somebody other than the user.
        "read_sms", "read_call_log", "scan_code", "nfc_read",
    }
    missing = sorted(e for e in expected if not ACTIONS.get(e, {}).get("untrusted_output"))
    assert not missing, f"untrustedOutput is not set on: {missing}"


def test_every_action_is_registered_in_builtins():
    src = (BUILTIN / "Builtins.kt").read_text()
    listed = src.split("fun all(): List<JarvisAction>", 1)[1].split("\n    }", 1)[0]

    # Two bundles are pulled in wholesale; anything inside their own
    # `val all = listOf(...)` counts as registered.
    bundled = ""
    for name in ("MediaActions.kt", "UiDelegatedActions.kt"):
        text = (BUILTIN / name).read_text()
        for m in re.finditer(r"val all: List<JarvisAction> = listOf\((.*?)\)", text, re.S):
            bundled += m.group(1)
    assert "UiActions.all" in listed and "MediaActions.all" in listed, (
        "Builtins.all() no longer pulls in the media / UI bundles"
    )

    for owner in sorted({v["owner"] for v in ACTIONS.values()}):
        if owner in ("DelegatedUiAction", "MediaKeyAction"):
            continue  # anonymous instances, checked via their bundle below
        assert re.search(rf"\b{owner}\b", listed + bundled), (
            f"{owner} is an action nobody registers in Builtins.all()"
        )

    # And the bundles themselves must not have lost a member.
    for action_id in ("media_play", "media_stop", "set_media_volume"):
        assert action_id in ACTIONS
    for action_id in ("ui_click", "ui_type", "take_screenshot"):
        assert action_id in ACTIONS


def test_the_docs_state_the_tier_the_code_enforces():
    doc = DOCS.read_text()
    checked = 0
    for line in doc.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        ids = re.findall(r"`([a-z0-9_]+)`", cells[0])
        digits = re.findall(r"\d", cells[1])
        if not ids or not digits:
            continue
        documented = TIER_ORDER[int(digits[0]) - 1]
        for action_id in ids:
            if action_id not in ACTIONS:
                continue
            assert ACTIONS[action_id]["tier"] == documented, (
                f"docs/actions.md says {action_id} is tier {digits[0]} "
                f"({documented}); the code enforces {ACTIONS[action_id]['tier']}"
            )
            checked += 1
    assert checked >= 30, f"only {checked} rows cross-checked; the doc table moved"


def test_every_action_is_documented():
    doc = DOCS.read_text()
    missing = sorted(a for a in ACTIONS if f"`{a}`" not in doc)
    assert not missing, f"undocumented actions: {missing}"


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed ({len(ACTIONS)} actions)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
