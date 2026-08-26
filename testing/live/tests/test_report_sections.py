"""The report's three read-from-a-file sections never invent a number."""

from __future__ import annotations

import json
from pathlib import Path

from testing.live.write_report import exploratory_section, migration_section, motion_section


def test_a_missing_exploratory_run_says_so(tmp_path: Path) -> None:
    lines = exploratory_section(tmp_path / "missing.json")
    assert any("Not run" in line for line in lines)
    assert not any("|" in line and "ok" in line for line in lines)


def test_the_exploratory_table_names_what_the_judge_doubted(tmp_path: Path) -> None:
    path = tmp_path / "exploratory.json"
    path.write_text(json.dumps({
        "target": "stack",
        "turns": 3,
        "conversations": [
            {"name": "ambiguous-room", "audit": "§7.5", "suspect": True,
             "turns": [{"ok": True}, {"ok": False, "why": "acted on the lab without asking"}]},
            {"name": "repeat-yourself", "audit": "§0", "suspect": False, "turns": [{"ok": True}]},
        ],
    }))
    lines = exploratory_section(path)
    table = "\n".join(lines)
    assert "2 unscripted conversations, 3 turns" in table
    assert "| ambiguous-room | §7.5 | 2 | **look** | acted on the lab without asking |" in table
    assert "| repeat-yourself | §0 | 1 | ok | — |" in table


def test_the_motion_section_reports_the_measurement_or_its_absence(tmp_path: Path) -> None:
    assert any("Not measured" in line for line in motion_section(tmp_path / "none.json"))
    path = tmp_path / "motion.json"
    path.write_text(json.dumps({
        "moving": {"frames": 115, "long": 4, "worst": 41.2},
        "still": {"frames": 115, "long": 0, "worst": 17.1},
        "cls": 0.002,
        "reduced_running": 0,
    }))
    text = "\n".join(motion_section(path))
    assert "| the voice screen, booting and breathing | 115 | 4 | 41.2 ms |" in text
    assert "layout shift over the boot sequence: 0.002" in text
    assert "0 animations running" in text


def test_the_migration_section_counts_rows_and_pictures(tmp_path: Path) -> None:
    doc = tmp_path / "UI_MIGRATION.md"
    doc.write_text("# x\n## 3. The inventory\n- [x] one\n- [ ] two · **partial**\n- [x] three\n## 4. What clean\n")
    shots = tmp_path / "ui-review"
    for screen in ("hud", "house-devices"):
        (shots / screen).mkdir(parents=True)
        for width in ("mobile", "tablet", "desktop"):
            (shots / screen / f"{width}.png").write_bytes(b"png")
    passed = tmp_path / "console_pass.json"
    passed.write_text(json.dumps({"console": "http://c", "results": [
        {"path": "/", "failures": []},
        {"path": "/house/devices", "failures": ["prose in mono: ['x']"]},
    ]}))
    text = "\n".join(migration_section(doc, shots, passed))
    assert "**2 of 3** rows migrated" in text
    assert "6 screenshots across 2 screens" in text
    assert "- two · **partial**" in text
    assert "1/2 routes rendered" in text
    assert "| /house/devices | prose in mono" in text
