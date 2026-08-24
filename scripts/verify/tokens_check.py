#!/usr/bin/env python3
"""design/tokens.json is the one place a design value is typed by a human.

Checks that it exists, parses, uses DTCG leaves (`{"$value": …, "$type": …}`),
has the six groups the target names with a minimum count each, and that every
colour value is a 6/8-digit hex, an rgba() (alpha tokens: wash, glow) or an
alias (`{color.focus}`).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "design" / "tokens.json"
NEED = {"color": 24, "type": 8, "space": 5, "radius": 3, "elevation": 3, "motion": 6}

if not SRC.is_file():
    print(f"missing {SRC.relative_to(ROOT)}")
    sys.exit(1)
try:
    tokens = json.loads(SRC.read_text())
except ValueError as exc:
    print(f"{SRC.relative_to(ROOT)}: invalid JSON: {exc}")
    sys.exit(1)


def leaves(node):
    if isinstance(node, dict):
        if "$value" in node:
            return [node]
        return [leaf for key, value in node.items() if not key.startswith("$") for leaf in leaves(value)]
    return []


problems: list[str] = []
counts = {group: len(leaves(tokens.get(group, {}))) for group in NEED}
for group, minimum in NEED.items():
    if counts[group] < minimum:
        problems.append(f"group {group!r}: {counts[group]} tokens, need at least {minimum}")
for leaf in leaves(tokens.get("color", {})):
    value = str(leaf["$value"])
    if (
        not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value)
        and not re.fullmatch(r"rgba?\(\s*\d+,\s*\d+,\s*\d+(?:,\s*[\d.]+)?\s*\)", value)
        and not re.fullmatch(r"\{[\w.-]+\}", value)
    ):
        problems.append(f"colour value is neither hex, rgba() nor alias: {value}")

print(f"{sum(counts.values())} tokens: " + ", ".join(f"{g}={n}" for g, n in counts.items()))
if problems:
    print("\n".join(problems))
    sys.exit(1)
