#!/usr/bin/env bash
# M59 — Anything online, locally.
#
# The brief: "genuinely capable of anything online". Search, fetch, crawl,
# browse and deep research existed; what did not was TIME — watch a page and
# say when it changes, follow feeds, "tell me when …" as a question asked
# again until the answer is yes — and a reader that gives the model a page as
# text whether or not the page needs JavaScript. All of it local: the fetches
# go through jarvis-browser or this process, the snapshots live under the
# config directory, and the change lands as a moment. Fails first: the
# integration does not exist.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M59" "anything online, locally"

require_file jarvis-core/jarvis/integrations/watch/__init__.py
require_file jarvis-core/tests/test_watch.py
require_file testing/live/scenarios/watch-page-change.yaml

check "the model has the verbs: watch a page, follow a feed, tell me when, read a page, what is new" python3 -c '
from pathlib import Path
src = Path("jarvis-core/jarvis/integrations/watch/__init__.py").read_text()
for name in ("watch_page", "watch_feed", "watch_for", "list_watches", "cancel_watch", "read_page", "feed_latest"):
    assert f"name=\"{name}\"" in src, f"no {name} tool"
print("seven tools")
'
check "a change lands as a moment and a bus event, never only a log line" bash -c 'grep -q "jarvis_watch_changed" jarvis-core/jarvis/integrations/watch/__init__.py && grep -q "async_add" jarvis-core/jarvis/integrations/watch/__init__.py'
check "a feed is parsed without a new dependency (RSS 2.0 and Atom, stdlib xml)" bash -c 'grep -q "ElementTree\|xml.etree" jarvis-core/jarvis/integrations/watch/__init__.py && ! grep -q "feedparser" jarvis-core/requirements.txt'
check "a watch never fetches faster than its floor, and the floor is written down" bash -c 'grep -q "MIN_INTERVAL" jarvis-core/jarvis/integrations/watch/__init__.py'
check "the reader survives JavaScript: jarvis-browser when configured, this process when not" bash -c 'grep -q "web.*fetch\|\"fetch\"" jarvis-core/jarvis/integrations/watch/__init__.py && grep -q "html.parser\|HTMLParser" jarvis-core/jarvis/integrations/watch/__init__.py'
check "the rig's router knows the verbs as one capability" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.capability import TOOL_CAPABILITY
for name in ("watch_page", "watch_feed", "watch_for", "read_page", "feed_latest"):
    assert TOOL_CAPABILITY.get(name) == "online", name
print("online")
'
check "the scenario changes a fixture page and expects the moment" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.scenario import load_all
s = [x for x in load_all() if x.name == "watch-page-change"][0]
assert s.capability == "online" and s.gated_on == "M59" and s.ground == "fixture"
assert any(t.do.get("fixture_write") for t in s.turns), "no fixture_write do: action"
assert any(t.expect.get("notification", {}).get("kind") == "watch" for t in s.turns), "no watch moment expected"
print("parses; writes the page; expects the moment")
'
check "the rig can rewrite a fixture page for a scenario" grep -q "fixture_write" testing/live/runner.py
check "watch: is switched on in the deployed config" python3 -c '
from pathlib import Path
text = Path("jarvis-core/config/configuration.yaml").read_text()
assert "\nwatch:\n" in text, "no watch: block — the integration never loaded"
print("switched on")
'
check_pytest "the watch tests: page change, no change, feed, question, reader (plain and browser), a malformed feed, the floor, persistence" 'cd jarvis-core && python3 -m pytest tests/test_watch.py -q --timeout=120 --timeout-method=signal -p no:cacheprovider'
# The Atom namespace is a name, not a fetch; every other host is.
check_not "no network in the watch tests" bash -c 'grep -nE "https?://(www\.)?[a-z0-9.-]+\.(com|org|net|io)/" jarvis-core/tests/test_watch.py | grep -v "w3.org/2005/Atom" | grep -q .' 
check_pytest "packaging still agrees (the new config block is read)" 'cd jarvis-core && python3 -m pytest tests/test_packaging.py -q --timeout=300 -p no:cacheprovider -k "silently_ignored or shipped"'
check "ruff is clean" bash -c "cd jarvis-core && python3 -m ruff check jarvis/integrations/watch tests/test_watch.py && cd .. && python3 -m ruff check testing/live"
# --- live ---------------------------------------------------------------------
# The integrator's line: on the fixture ground the rig rewrites a page it
# serves, and the watch the user asked for lands as a moment.
check_sh "live: 'watch this page and tell me when it changes' → the page changes → a moment" \
    'LIVE_TARGET=harness LIVE_NO_BROWSER=1 LIVE_CAPABILITY=online bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'
verify_end
