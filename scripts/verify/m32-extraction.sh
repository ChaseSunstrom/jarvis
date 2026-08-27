#!/usr/bin/env bash
# M32 — crawling and document extraction. The measurement, the decision, and
# the capability that came out of it: tables that keep their rows, and PDFs and
# Word files read as text.
source "$(dirname "$0")/lib.sh"
verify_begin "M32" "crawling and document extraction"
use_venv

require_file jarvis-browser/jarvis_browser/documents.py
require_file testing/live/fixtures/handbook/warranty.pdf
require_file testing/live/fixtures/handbook/service-record.docx
require_file testing/live/fixtures/handbook/tariff.html
require_file testing/live/scenarios/research-reads-a-document.yaml

# The decision, with the numbers that made it. A rejection with no measurement
# behind it is an opinion, and the next person re-litigates it.
check "Crawl4AI was measured on this host, not read about" python3 -c '
from pathlib import Path
text = Path("docs/TOOLING_DECISIONS.md").read_text()
for needle in ("4.23 GB", "411 MB", "SSRF"):
    assert needle in text, f"the Crawl4AI decision does not record {needle!r}"
print("image size, resident size and the loopback refusal are all recorded")
'
check "Docling was resolved before it was rejected" python3 -c '
from pathlib import Path
text = Path("docs/TOOLING_DECISIONS.md").read_text()
assert "101" in text and "CUDA" in text, "the Docling decision has no numbers in it"
print("101 packages including the CUDA stack, on a host with no GPU")
'
check "what was built instead is named, and so is what it does not do" python3 -c '
from pathlib import Path
text = Path("docs/TOOLING_DECISIONS.md").read_text()
assert "does NOT buy" in text and "no OCR" in text
print("no OCR, no link-following: written down rather than discovered later")
'

# Tables. The regression that mattered: every figure preserved and not one row.
check_sh "a table keeps its rows" 'cd jarvis-browser && python3 -c "
from jarvis_browser.extract import extract
from pathlib import Path
text = extract(Path(\"../testing/live/fixtures/handbook/tariff.html\").read_text()).text
assert \"| Night | 00:30–07:30 | 7.9 p/kWh | included |\" in text, text
assert \"| --- |\" in text
print(\"the night rate is still in the same row as its hours and its price\")
"'

check_pytest "documents are read, and an unreadable one says so" 'python3 -m pytest jarvis-browser/tests/test_documents.py -q --timeout=60 \
        --timeout-method=signal'
check "a scanned PDF is named rather than returned empty" \
    grep -q 'no text layer' jarvis-browser/jarvis_browser/documents.py
check "document text is fenced as untrusted, like a page" \
    grep -q 'content_is_untrusted' jarvis-browser/jarvis_browser/app.py
# A requirement LINE, not the word: every one of these files explains at
# length why docling is not here, and the first version of this check failed on
# its own documentation.
check_not "docling is not a dependency of anything here" \
    grep -rqE '^[[:space:]]*docling([=<>~[]|$)' jarvis-browser/requirements.txt \
        jarvis-core/requirements.txt testing/requirements.txt

# The running service, on the repository's own fixtures.
check_sh "the running browser reads a PDF and a Word file" \
    'python3 - <<PY
import sys, json, urllib.request
sys.path.insert(0, ".")
from testing.live.browser_service import SharedBrowser
from testing.live.web import FixtureWeb

web = FixtureWeb(); web.start()
browser = SharedBrowser()
if not browser.start():
    web.stop()
    raise SystemExit(f"could not borrow jarvis-browser: {browser.why}")
site = web.sites[0].url
try:
    for path, needle in (("/warranty.pdf", "seven year"), ("/service-record.docx", "HH-4471")):
        request = urllib.request.Request(
            browser.url + "/fetch",
            data=json.dumps({"url": site + path}).encode(),
            headers={"authorization": f"Bearer {browser.token}",
                     "content-type": "application/json"},
        )
        body = json.loads(urllib.request.urlopen(request, timeout=90).read())
        assert needle in body["text"], f"{path}: {body[chr(116)+chr(101)+chr(120)+chr(116)][:200]!r}"
        print(f"{path}: {body[chr(107)+chr(105)+chr(110)+chr(100)]}, {body[chr(99)+chr(104)+chr(97)+chr(114)+chr(95)+chr(99)+chr(111)+chr(117)+chr(110)+chr(116)]} chars")
finally:
    browser.stop(); web.stop()
PY'

# The eval, which is where the numbers in the decision come from. Six questions
# now: the two added here are the two shapes the old fetcher could not answer.
check_sh "the research eval passes, including the table and the PDF" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     timeout 3000 python3 evals/research_eval.py --backend fixture --out .verify/research 2>&1 \
       | grep -v onnxruntime | tail -3'
check "and it says which browser produced those numbers" \
    grep -q 'fetch through' evals/research_eval.py

check_sh "an answer that is only in a document is found by talking" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     timeout 900 python3 -m testing.live.runner --full --only research-reads-a-document \
       --no-browser --target harness 2>&1 | grep -v onnxruntime | tail -3'
verify_end
