#!/usr/bin/env bash
# M107 — settings rows line up: one grid, the same column edges on every tab.
set -u
cd "$(dirname "$0")/../.."
. scripts/verify/lib.sh
verify_begin "M107" "settings rows line up"
use_venv

check "one SettingRow, used by every Settings section" python3 -c '
from pathlib import Path
row = Path("jarvis-web/src/lib/ui/SettingRow.svelte")
assert row.exists(), "no SettingRow component"
sections = ["Assistant", "SettingsVoice", "SettingsHouse", "SettingsConsole", "Tools"]
missing = [s for s in sections if "<SettingRow" not in Path(f"jarvis-web/src/lib/sections/{s}.svelte").read_text()]
assert not missing, "sections without SettingRow: " + ", ".join(missing)
print("SettingRow in", ", ".join(sections))
'
ensure_web_build
run_playwright "every value cell shares one left edge across the five tabs, and controls in a panel share a width" e2e/settings-layout.spec.ts
verify_end
