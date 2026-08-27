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
users = {
  "SettingPlain": "jarvis-web/src/lib/components/SettingPlain.svelte",
  "SettingRaw": "jarvis-web/src/lib/components/SettingRaw.svelte",
  "Models": "jarvis-web/src/lib/components/Models.svelte",
  "SettingsVoice": "jarvis-web/src/lib/sections/SettingsVoice.svelte",
  "SettingsHouse": "jarvis-web/src/lib/sections/SettingsHouse.svelte",
  "SettingsConsole": "jarvis-web/src/lib/sections/SettingsConsole.svelte",
}
missing = [n for n, path in users.items() if "<SettingRow" not in Path(path).read_text()]
assert not missing, "without SettingRow: " + ", ".join(missing)
# and nothing draws its own settings grid any more
own = [n for n, path in users.items() if "grid-template-columns: minmax(12rem" in Path(path).read_text()]
assert not own, "still drawing their own grid: " + ", ".join(own)
print("SettingRow in", ", ".join(users))
'
ensure_web_build
run_playwright "every value cell shares one left edge across the five tabs, and controls in a panel share a width" e2e/settings-layout.spec.ts
verify_end
