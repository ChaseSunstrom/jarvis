#!/usr/bin/env python3
"""The phone's activity strip speaks the console's vocabulary (M61).

`tests/contracts/activity_rows.json` is the one table: the bus events that
make a row, the kind each makes, the cap, the sensor domains and the states.
`jarvis-web/src/lib/activity.test.ts` holds the console to it; this holds
`assist/ActivityRows.kt`. Read the Kotlin as text — the JVM is not here — and
compare the literal tables, which is what "mirror" means in this repository.

Run:  python3 android-app/tools/activity_mirror_test.py
      python3 -m pytest android-app/tools/activity_mirror_test.py -q
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KT = ROOT / "app/src/main/kotlin/ai/jarvis/app/assist/ActivityRows.kt"
STRIP = ROOT / "app/src/main/kotlin/ai/jarvis/app/ui/ActivityStrip.kt"
CLIENT = ROOT / "app/src/main/kotlin/ai/jarvis/app/assist/AssistPipelineClient.kt"
CONTRACT = ROOT.parent / "tests/contracts/activity_rows.json"


def contract() -> dict:
    return json.loads(CONTRACT.read_text())


def kotlin_events() -> dict[str, str]:
    src = KT.read_text()
    block = src[src.index("val EVENTS"): src.index(")", src.index("linkedMapOf("))]
    return {m.group(1): m.group(2).lower() for m in re.finditer(r'"([a-z_]+)" to Kind\.([A-Z]+)', block)}


def test_the_events_and_kinds_are_the_contracts():
    want = contract()["events"]
    have = kotlin_events()
    assert have == want, f"ActivityRows.EVENTS differs from the contract: {sorted(set(have.items()) ^ set(want.items()))}"


def test_the_kinds_and_states_are_the_contracts():
    src = KT.read_text()
    kinds = re.search(r"enum class Kind \{ ([A-Z, ]+) \}", src).group(1).replace(" ", "").lower().split(",")
    states = re.search(r"enum class State \{ ([A-Z, ]+) \}", src).group(1).replace(" ", "").lower().split(",")
    assert sorted(kinds) == sorted(contract()["kinds"]), kinds
    assert sorted(states) == sorted(contract()["states"]), states


def test_the_cap_and_the_sensor_domains_are_the_contracts():
    src = KT.read_text()
    cap = int(re.search(r"const val CAP = (\d+)", src).group(1))
    assert cap == contract()["cap"], cap
    domains = re.findall(r'"([a-z_]+)"', src[src.index("val SENSOR_DOMAINS"): src.index(")", src.index("val SENSOR_DOMAINS"))])
    assert sorted(domains) == sorted(contract()["sensor_domains"]), domains


def test_every_event_in_the_table_is_handled_and_a_press_is_a_row_every_time():
    src = KT.read_text()
    for event in contract()["events"]:
        assert f'"{event}"' in src[src.index("fun rowFrom"):], f"{event} is in the table but not in rowFrom"
    # Two presses of the same button are two rows: the id carries the time.
    press = src[src.index('"jarvis_mqtt_event" ->'): src.index('"vision_look_started" ->')]
    assert "at" in press and "press:" in press


def test_the_client_subscribes_to_the_whole_vocabulary_and_feeds_it_on():
    client = CLIENT.read_text()
    assert "ActivityRows.EVENTS.keys" in client, "the subscription still names only the tool events"
    assert "callbacks.onBusEvent(type, data)" in client


def test_the_strip_only_paints():
    strip = STRIP.read_text()
    assert "class ActivityStrip" in strip and "fun render(rows: ActivityRows)" in strip
    assert "JarvisUi." in strip and not re.search(r"Color\.parseColor|0x[0-9A-Fa-f]{6,8}", strip), "a colour typed by hand"


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
