#!/usr/bin/env bash
# M67 — Settings under approval.
#
# The operator's question, verbatim: "how can I ask it to be able to edit
# settings with permission". Asked to enable "demo mode" the model asked what
# that meant — right, there is no such setting — but it could not have said
# what the settings ARE, because it had no tool for any of it. Now it has two:
# `list_settings` (Tier 1, read-only) over the registry the console's Settings
# screens read, and `change_setting` (Tier 3) whose approval carries the exact
# key, the coerced value and the value it replaces, pinned, with one sentence
# composed from them — and which writes through the SAME function the
# console's `config/settings/set` is, so the validation, the audit line and
# the event are one sequence with no second door to be laxer on.
#
# Every claim is checked here, failing-first: on the branch before M67 every
# check below is red. The tools are read off a cold registry, the one write
# path is counted in the source, the contract is read on both sides, the
# banner is read off the Svelte, the mock off its source, the docs by their
# needles, and then the suites, the harness against a real jarvis-core, and
# the console in a browser.
#
# No `set -e`: lib.sh's contract is that a failing check does not stop the
# run, so the summary names every missing piece rather than the first.
. "$(dirname "$0")/lib.sh"
verify_begin "M67" "settings under approval"
use_venv

require_file jarvis-core/tests/test_settings_tool.py
require_file jarvis-web/src/lib/approvals.ts
require_file jarvis-web/e2e/approvals.spec.ts

# --- the tools, read off a cold registry ------------------------------------
check "list_settings is Tier 1 and read-only; change_setting is Tier 3, pinned, summarised, and held (not refused) on a tainted turn" python3 -c '
import sys, tempfile
sys.path.insert(0, "jarvis-core")
from jarvis.core import Jarvis
from jarvis.llm.tools import READ_ONLY_TOOLS, REFUSE_WHEN_TAINTED, TIER_APPROVAL, TIER_DIRECT, ToolRegistry, register_builtin_tools
with tempfile.TemporaryDirectory() as tmp:
    reg = ToolRegistry(Jarvis(tmp))
    register_builtin_tools(reg)
    ls, cs = reg.get("list_settings"), reg.get("change_setting")
    assert ls is not None and cs is not None, "a settings tool is not registered"
    assert ls.tier == TIER_DIRECT and reg.is_read_only(ls) and "list_settings" in READ_ONLY_TOOLS
    assert cs.tier == TIER_APPROVAL and not reg.is_read_only(cs)
    assert cs.pin is not None and cs.summarise is not None, "change_setting is not pinned, or has no sentence"
    assert "change_setting" not in REFUSE_WHEN_TAINTED, "a tainted turn is refused rather than held"
    for name in ("key", "value"):
        assert name in cs.schema()["function"]["parameters"]["required"], name
print("list_settings: tier 1, read-only · change_setting: tier 3, pin + sentence, held when tainted")
' 2>/dev/null
check "the allowlist holds none of the keys the safety model reads, and no spelling of them resolves" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.settings import SETTINGS, SETTINGS_BY_KEY, matching_settings, nearest_settings
forbidden = ("llm.expose", "expose", "jarvis.http.host", "jarvis.http", "cors_allowed_origins", "local_only", "network_mode", "mcp.allow_stdio", "allow_stdio")
for key in forbidden:
    assert key not in SETTINGS_BY_KEY and matching_settings(key) == [], f"{key!r} resolves to a setting"
assert not any(s.key.startswith(("jarvis.http", "mcp.")) or "expose" in s.key for s in SETTINGS)
assert all(s.note.strip() for s in SETTINGS), "a setting has no note for list_settings to show"
twice = [nearest_settings("demo mode") for _ in range(2)]
assert twice[0] == twice[1] and twice[0] and all(k in SETTINGS_BY_KEY for k in twice[0]), twice
print(f"{len(SETTINGS)} settings, every one with a note; demo mode -> " + ", ".join(twice[0][:3]) + " …")
'
check "the model is told to look settings up before saying one does not exist, and to name the real one" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.llm.agent import TOOL_RULES
rule = " ".join(TOOL_RULES.split())
assert "call list_settings first" in rule, "no rule to look settings up"
assert "name the nearest real" in rule, "no rule to say the real name"
assert "change_setting" in rule and "approval" in rule, "the rule does not say a change waits for approval"
print("in TOOL_RULES")
'

# --- one write path, counted in the source ----------------------------------
check "the console command, the REST route and the tool all call async_set_setting, and nothing else writes the overlay" python3 -c '
import re
from pathlib import Path
core = Path("jarvis-core/jarvis")
writers = [str(p) for p in core.rglob("*.py") if "settings.async_set(" in p.read_text()]
assert writers == ["jarvis-core/jarvis/api/common.py"], f"the overlay is written from {writers}"
common = (core / "api/common.py").read_text()
assert common.count("settings.async_set(") == 1 and common.count("settings.async_reset(") == 1
assert "_SETTINGS_AUDIT = logging.getLogger(\"jarvis.settings.audit\")" in common
assert common.count("_record_setting_change(") == 3, "the audit+event is not fired from both set and reset, once each"
ws = (core / "api/websocket.py").read_text()
assert re.search(r"_cmd_settings_set.*?common\.async_set_setting\(self\.jarvis, msg, context=self\._context\(\)\)", ws, re.S)
rest = (core / "api/rest.py").read_text()
assert "common.async_set_setting(" in rest and "context=_context(token)" in rest
tools = (core / "llm/tools.py").read_text()
block = tools[tools.index("async def _change_setting"):tools.index("registry.register(\n        name=\"change_setting\"")]
assert "await async_set_setting(" in block and "context=context" in block
assert "jarvis.settings.values[" not in tools and "settings.async_set" not in tools, "the tool writes the overlay itself"
print("one writer in api/common.py; websocket, REST and change_setting all go through it, each with who")
'
check "the event is declared once and carries what a listener needs" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.const import EVENT_SETTING_CHANGED
assert EVENT_SETTING_CHANGED == "jarvis_setting_changed"
from pathlib import Path
src = Path("jarvis-core/jarvis/api/common.py").read_text()
fire = src[src.index("jarvis.bus.fire(\n        EVENT_SETTING_CHANGED"):]
fire = fire[:fire.index(")\n\n")]
for key in ("\"key\"", "\"label\"", "\"previous\"", "\"value\"", "\"applied\"", "\"restart_required\"", "\"origin\"", "\"action\""):
    assert key in fire, f"the event lacks {key}"
print("jarvis_setting_changed: key, label, previous, value, applied, restart_required, origin, action")
'

# --- the contract, both halves ----------------------------------------------
check "the tier contract names the summary field, and both suites read it" python3 -c '
import json
from pathlib import Path
rule = json.loads(Path("tests/contracts/tool_tiers.json").read_text())["rules"]["held_summary"]
assert rule["field"] == "summary" and "PINNED" in rule["means"]
assert "held_summary" in Path("jarvis-core/tests/test_tool_tiers_contract.py").read_text()
web = Path("jarvis-web/src/lib/tierContract.test.ts").read_text()
assert "held_summary" in web and "SUMMARY_FIELD" in web
lib = Path("jarvis-web/src/lib/approvals.ts").read_text()
assert "export const SUMMARY_FIELD = \x27summary\x27" in lib
print("tool_tiers.json rules.held_summary.field == summary, read by test_tool_tiers_contract.py and tierContract.test.ts")
'

# --- the surface, read off the Svelte and the mock --------------------------
check "the banner draws the sentence as the headline, the tool name under it, and the raw arguments only without one" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/components/Approvals.svelte").read_text()
assert "from \x27$lib/approvals\x27" in src, "the banner does not read the shared helper"
assert "function summarise(" not in src, "a second, local rendering of the arguments survives"
markup = re.sub(r"<!--.*?-->", "", src, flags=re.S)
for needle in ("approval-summary-", "approval-name-", "approval-tool-", "approval-args-"):
    assert needle in markup, f"no {needle} test id"
assert markup.index("{#if summaryOf(req)}") < markup.index("approval-args-"), "the raw line is not behind the sentence"
assert "{headlineOf(req)}" in markup
assert "summary?: string" in Path("jarvis-web/src/lib/jarvisClient.ts").read_text(), "PendingApproval has no summary"
print("headline = summary || tool; tool name under a sentence; key: value only without one")
'
check "the mock carries summary, label and previous, offers both tools, and writes the row on approval" python3 -c '
from pathlib import Path
src = Path("tests/web/mock-ha.mjs").read_text()
hook = src[src.index("case \x27test/raise_approval\x27"):src.index("case \x27jarvis/test/last_answer\x27")]
assert "summary:" in hook and "tainted:" in hook, "the raise hook carries no summary"
for name in ("name: \x27list_settings\x27", "name: \x27change_setting\x27"):
    assert name in src, f"the mock toolbox lacks {name}"
call = src[src.index("if (tool.name === \x27change_setting\x27)"):src.index("broadcast(\x27jarvis_approval_required\x27, {\n\t\t\t\t\t\t\trequest_id: requestId")]
assert "no setting called" in call and "previous: row.value" in call
approve = src[src.index("case \x27jarvis/approve\x27"):src.index("case \x27test/raise_approval\x27")]
assert "req.tool === \x27change_setting\x27" in approve and "jarvis_setting_changed" in approve
setcase = src[src.index("case \x27config/settings/set\x27"):src.index("case \x27config/settings/reset\x27")]
assert "label: row.label" in setcase and "previous," in setcase
print("raise hook + toolbox + approve + settings/set, in the server\x27s shape")
'

# --- the docs ---------------------------------------------------------------
check "the docs carry it: clients.md, security.md, CHANGELOG, verification, MILESTONES, OVERHAUL_PLAN, AUDIT" python3 -c '
from pathlib import Path
checks = {
    "jarvis-core/docs/clients.md": ("`list_settings`", "`jarvis.settings.audit`", "`jarvis_setting_changed`", "`summary`"),
    "docs/security.md": ("## A settings tool may change what the settings page can change, and nothing else", "`change_setting`", "`list_settings`"),
    "CHANGELOG.md": ("**M67 — settings under approval.**",),
    "docs/verification.md": ("### Settings under approval (M67)",),
    "MILESTONES.md": ("**M67 — Settings under approval**",),
    "docs/OVERHAUL_PLAN.md": ("| M67 |",),
    "docs/AUDIT.md": ("`jarvis_setting_changed`",),
}
for path, needles in checks.items():
    text = Path(path).read_text()
    for needle in needles:
        assert needle in text, f"{path} lacks: {needle}"
print(f"{len(checks)} documents")
'

# --- the suites --------------------------------------------------------------
check "ruff" python3 -m ruff check jarvis-core/jarvis/llm/tools.py jarvis-core/jarvis/llm/agent.py jarvis-core/jarvis/settings.py jarvis-core/jarvis/api jarvis-core/tests/test_settings_tool.py jarvis-core/tests/test_tool_tiers_contract.py testing/e2e/test_harness_selftest.py
check_sh "core: the settings tools, the contract, the settings API, the registry and ask_user" \
    'cd jarvis-core && python3 -m pytest tests/test_settings_tool.py tests/test_tool_tiers_contract.py tests/test_settings_api.py tests/test_llm_tools.py tests/test_ask_user.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
check_sh "the harness, against a real jarvis-core: the scripted model asks, a human approves, the setting changes, the audit says so; demo mode is refused with the nearest" \
    'python3 -m pytest testing/e2e/test_harness_selftest.py -q -k "setting" 2>&1 | tail -2'
check "token lint: no new hard-coded value" python3 scripts/verify/token_lint.py
check "every screen is declared and uses ScreenState" python3 scripts/verify/web_states_check.py
check "no dead controls" node scripts/verify/web_dead_controls.mjs
ensure_web_deps
ensure_web_build
check_sh "svelte-check finds nothing" 'cd jarvis-web && npx svelte-check --threshold error 2>&1 | tail -1'
check_sh "the console's unit tests, the tier contract among them" 'cd jarvis-web && npx vitest run 2>&1 | tail -3'
run_playwright "the banner: a sentence for a setting that lands on the Settings page, name-and-arguments for the rest, held from the Tools page" approvals.spec.ts
run_playwright "the approval path the rest of the suite already proves, unchanged" 'e2e.spec.ts hud.spec.ts -g "held action"'

verify_end
