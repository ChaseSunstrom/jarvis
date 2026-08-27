#!/usr/bin/env python3
"""The house's way into PHONE TASKS (M98's fifth item), pinned from the outside.

The phone's task store could import a bundle for months — `TaskStore.import`
— and nothing ever called it: PHONE TASKS said tasks "arrive from jarvis-core"
and none could. The way in is a device action, `import_tasks`, run like any
other `device_command`; the house's side is the `phone-tasks` skill, which is
where the model learns the format. This mirror pins the three ends to each
other: the action is registered and tier 3; the skill exists, names the
action, and its vocabularies are the phone's (every trigger id it lists is a
`TriggerIds` constant, every step type a `StepType`, every phone action id in
its example a registered builtin); the settings hint tells the truth.

What it cannot check is that a phone screens the bundle — that is
`TaskActionsTest` (Robolectric), and the M98 gate's fake phone on the house.

Run:  python3 android-app/tools/phone_tasks_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app"
BUILTIN = APP / "automation/actions/builtin"
SKILL = ROOT / "jarvis-core/config/skills/phone-tasks/SKILL.md"

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))


def builtin_ids() -> dict[str, str]:
    """action id -> tier, from every `object X : JarvisAction` in builtin/."""
    out: dict[str, str] = {}
    for path in sorted(BUILTIN.glob("*.kt")):
        src = path.read_text()
        for m in re.finditer(r"\n(?:object|internal object) (\w+) : JarvisAction(.*?)(?=\n(?:object|internal object|/\*\*|$))", src, re.S):
            body = m.group(2)
            aid = re.search(r'override val id\s*=\s*"([^"]+)"', body)
            tier = re.search(r"override val tier\s*=\s*ActionTier\.(\w+)", body)
            if aid and tier:
                out[aid.group(1)] = tier.group(1)
    return out


def main() -> int:
    ids = builtin_ids()
    check("import_tasks is a registered builtin", "import_tasks" in ids, str(sorted(ids)[:5]))
    check("import_tasks is tier 3 (CONFIRM): installing behaviour is seen once, on the phone",
          ids.get("import_tasks") == "CONFIRM", ids.get("import_tasks", "missing"))
    check("list_tasks is a registered builtin, tier 1", ids.get("list_tasks") == "AUTO", ids.get("list_tasks", "missing"))

    builtins = (BUILTIN / "Builtins.kt").read_text()
    listed = builtins.split("fun all(): List<JarvisAction>", 1)[1].split("\n    }", 1)[0]
    check("both are in Builtins.all()", "ImportPhoneTasks" in listed and "ListPhoneTasks" in listed)

    actions = (BUILTIN / "TaskActions.kt").read_text()
    check("import_tasks hands the bundle to TaskStore.import with fromServer = true",
          "import(bundle, fromServer = true)" in actions or ".import(bundle, true)" in actions)
    check("a document never sets enabled_by_user: the store screens, the action does not",
          "enabledByUser" not in actions and "setEnabledByUser" not in actions)

    hint = (APP / "automation/ui/AutomationsActivity.kt").read_text()
    check("PHONE TASKS names the way in", "import_tasks action" in hint)

    skill = SKILL.read_text() if SKILL.exists() else ""
    check("the phone-tasks skill exists with frontmatter", skill.startswith("---\nname: phone-tasks\n"))
    check("the skill names the action and the tool", "import_tasks" in skill and "control_device" in skill)
    check("the skill narrows to the device tools", "allowed-tools: [list_my_devices, control_device]" in skill)

    trigger_src = next(APP.rglob("TriggerIds.kt"), None)
    if trigger_src is None:
        trigger_src = next(p for p in APP.rglob("*.kt") if "object TriggerIds" in p.read_text())
    trigger_ids = set(re.findall(r'const val \w+ = "([a-z_]+)"', trigger_src.read_text()))
    triggers_section = skill.split("## Triggers", 1)[1].split("## Steps", 1)[0]
    listed_triggers = set(re.findall(r"`([a-z_]+)`", triggers_section)) & {t for t in trigger_ids}
    named = set(re.findall(r"`([a-z_]+)`(?: \(| /|,|\.|$)", triggers_section, re.M))
    unknown = {n for n in named if n not in trigger_ids and n not in {"threshold", "direction", "at", "days", "minutes", "name", "lat", "lon", "radius_m", "packages", "below", "above", "mon", "tue"}}
    check("every trigger id the skill lists is one the phone has", not unknown, str(sorted(unknown)))
    check("the skill lists every trigger id the phone has", trigger_ids <= listed_triggers, str(sorted(trigger_ids - listed_triggers)))

    models = (APP / "automation/tasks/TaskModels.kt").read_text()
    step_types = set(re.findall(r'\w+\("([a-z_]+)"\)', models.split("enum class StepType", 1)[1].split("}", 1)[0]))
    steps_section = skill.split("## Steps", 1)[1].split("## What the phone", 1)[0]
    used = set(re.findall(r'"type": "([a-z_]+)"', steps_section))
    check("every step type the skill shows is one the phone runs", used <= step_types, str(sorted(used - step_types)))
    check("the skill shows every step type", step_types <= used, str(sorted(step_types - used)))

    example_actions = set(re.findall(r'"action": "([a-z_]+)"', skill))
    check("every action id in the skill's example is a registered builtin", example_actions <= set(ids), str(sorted(example_actions - set(ids))))
    quoted = set(re.findall(r"`([a-z_]+)`", steps_section)) - step_types - {"action", "list_my_devices", "params"}
    check("every phone action the skill names in its step list exists", quoted <= set(ids), str(sorted(quoted - set(ids))))

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(("  ok    " if ok else "  FAIL  ") + name + (f"\n        | {detail}" if not ok and detail else ""))
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
