#!/usr/bin/env python3
"""Phone automation is scaffolded and OFF — the Python mirror of that claim.

`PhoneAutomationFlagTest.kt` asserts it on the JVM. This asserts the parts a
unit test cannot see: that the flag exists with a `false` default in the build
file, that both Android services refuse to work while it is off, that the
bridge refuses the actions as well, and that the master switch in
`PolicyStore` starts off.

Run: ``python3 android-app/tools/phone_automation_flag_test.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "src" / "main" / "kotlin" / "ai" / "jarvis" / "app"
BUILD = ROOT / "app" / "build.gradle.kts"

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'  — ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(name)


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_flag_exists_and_is_false_by_default() -> None:
    text = source(BUILD)
    start = text.find('"PHONE_AUTOMATION"')
    check("the build file declares PHONE_AUTOMATION", start != -1)
    if start == -1:
        return
    # The value expression, to the end of the `buildConfigField(...)` call. It
    # is an override-or-default (`findProperty(...) ?: "false"`), so what is
    # asserted is the DEFAULT — the value every build that passes no flag gets.
    tail = text[start : start + 300]
    check(
        "and it defaults to false",
        re.search(r'\?:\s*"false"', tail) is not None or '"false",' in tail,
        " ".join(tail.split())[:80],
    )


def test_both_services_stand_down_while_it_is_off() -> None:
    for name, path in (
        ("accessibility service", APP / "automation/accessibility/JarvisAccessibilityService.kt"),
        ("notification listener", APP / "automation/notify/JarvisNotificationListener.kt"),
    ):
        text = source(path)
        check(f"{name} reads the flag", "BuildConfig.PHONE_AUTOMATION" in text)
        # Twice, deliberately: a service that was already connected when the
        # build changed under it never gets another `onServiceConnected`.
        check(
            f"{name} checks it on connect AND on every event",
            text.count("BuildConfig.PHONE_AUTOMATION") >= 2,
            f"{text.count('BuildConfig.PHONE_AUTOMATION')} check(s)",
        )


def test_the_bridge_refuses_the_actions_too() -> None:
    text = source(APP / "automation/AutomationBridge.kt")
    check("the bridge knows which actions are phone automation", "isPhoneAutomation" in text)
    check(
        "and refuses them before reaching a dispatcher",
        re.search(r"isPhoneAutomation\([^)]*\)\s*&&\s*!BuildConfig\.PHONE_AUTOMATION", text)
        is not None,
    )


def test_the_master_switch_starts_off() -> None:
    text = source(APP / "automation/policy/PolicyStore.kt")
    match = re.search(r'getBoolean\(KEY_ENABLED,\s*(\w+)\)', text)
    check("the automation master switch has a default", match is not None)
    if match:
        check("and it is off", match.group(1) == "false", match.group(1))


def test_the_interface_is_scaffolded_and_unreachable() -> None:
    path = APP / "automation/phone/PhoneAutomation.kt"
    check("the interface exists", path.is_file())
    if not path.is_file():
        return
    text = source(path)
    check("it is an interface, not an implementation", "interface PhoneAutomation" in text)
    check(
        "`available` is the flag and nothing else",
        re.search(r"val available: Boolean get\(\) = BuildConfig\.PHONE_AUTOMATION", text)
        is not None,
    )
    check(
        "a delegate cannot be read while the flag is off",
        "get() = if (available) field else null" in text,
    )
    # Nothing in the shipping app may reach it yet: this is a scaffold.
    callers = [
        p for p in APP.rglob("*.kt")
        if p.name != "PhoneAutomation.kt" and "PhoneAutomation.delegate" in source(p)
    ]
    check("nothing wires an implementation in", not callers, ", ".join(p.name for p in callers))


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print()
    if FAILURES:
        print(f"phone automation flag: {len(FAILURES)} FAILED")
        return 1
    print("phone automation: scaffolded, flagged OFF, and unreachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
