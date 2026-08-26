"""Every console route, opened against the running stack (M50).

The e2e suite proves the console on a mock backend. This opens the console
people actually use — the container on :8199, talking to the real jarvis-core
— and walks every route `jarvis-web/src/lib/screens.ts` declares: each has to
render its own probe, log no console error and no page error, and measure as
Reactor II — the body face, the ground, only palette colours on the panels,
no prose in mono, no grid, no brackets, no canvas.

    python3 testing/live/console_pass.py            # exit 1 on any failure
    LIVE_CONSOLE_URL=http://127.0.0.1:8199 python3 testing/live/console_pass.py

Writes `.verify/live/console_pass.json`, which `docs/LIVE_TEST_REPORT.md`
reads for its migration section.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCREENS = REPO_ROOT / "jarvis-web" / "src" / "lib" / "screens.ts"
TOKENS = REPO_ROOT / "design" / "tokens.json"
NODE_CWD = REPO_ROOT / "jarvis-web"
OUT = REPO_ROOT / ".verify" / "live" / "console_pass.json"

#: Console errors that are the environment's, not the page's. Each names why.
IGNORED = (
    # A headless browser with no microphone: the voice screen reports it and
    # types instead, which is the behaviour hud.spec.ts asserts.
    "NotFoundError",
    "Requested device not found",
    # Autoplay policy on a page nobody clicked.
    "play() failed",
)


def routes() -> list[dict[str, str]]:
    """Every screen with a real path, and the element that proves it rendered."""
    src = SCREENS.read_text(encoding="utf-8")
    out = []
    for block in re.findall(r"\{\n\t\tpath: .*?\n\t\}", src, re.S):
        path = re.search(r"path: '([^']+)'", block)
        name = re.search(r"name: '([^']+)'", block)
        probe = re.search(r"probe: '([^']+)'", block)
        if not (path and name and probe) or "[" in path.group(1):
            continue
        out.append({"path": path.group(1), "name": name.group(1), "probe": probe.group(1)})
    # `LIVE_CONSOLE_ROUTES=/,/house` narrows the pass to a milestone's own
    # screens; M50's gate still walks the whole console.
    only = [r.strip() for r in os.environ.get("LIVE_CONSOLE_ROUTES", "").split(",") if r.strip()]
    if only:
        return [r for r in out if r["path"] in only]
    return out


def token_colours() -> dict[str, str]:
    """`--jv-name` -> hex, for the colours the render is measured against."""
    tokens = json.loads(TOKENS.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for name, leaf in tokens["color"].items():
        if isinstance(leaf, dict) and "$value" in leaf:
            out[f"--jv-{name}"] = str(leaf["$value"])
    return out


def main() -> int:
    url = os.environ.get("LIVE_CONSOLE_URL", "http://127.0.0.1:8199").rstrip("/")
    job = {"url": url, "routes": routes(), "tokens": token_colours(), "timeoutMs": 30000}
    proc = subprocess.run(
        ["node", str(REPO_ROOT / "testing" / "live" / "browser_routes.cjs"), json.dumps(job)],
        cwd=str(NODE_CWD),
        capture_output=True,
        text=True,
        timeout=600,
    )
    line = (proc.stdout.strip().splitlines() or [""])[-1]
    try:
        answer = json.loads(line)
    except json.JSONDecodeError:
        print(f"the browser pass produced no answer:\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}")
        return 1
    if "error" in answer:
        print(f"the browser pass failed: {answer['error']}")
        return 1
    failures: list[str] = []
    for entry in answer["results"]:
        errors = [e for e in entry["errors"] if not any(word in e for word in IGNORED)]
        facts = entry.get("facts") or {}
        why = []
        if not entry["ok"]:
            why.append(f"did not render ({entry['note']})")
        if errors:
            why.append(f"{len(errors)} console error(s): {errors[0]}")
        if facts:
            if "barlow" not in facts.get("bodyFont", ""):
                why.append("body face is not Barlow")
            if not facts.get("ground"):
                why.append("the ground is not --jv-bg")
            if facts.get("grid"):
                why.append("the grid or brackets are drawn")
            if facts.get("canvas"):
                why.append("a canvas is drawing")
            if facts.get("offPalette"):
                why.append(f"colours off the palette: {facts['offPalette']}")
            if facts.get("monoProse"):
                why.append(f"prose in mono: {facts['monoProse']}")
        entry["failures"] = why
        if why:
            failures.append(f"{entry['path']}: " + "; ".join(why))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"console": url, "results": answer["results"]}, indent=2), encoding="utf-8")
    total = len(answer["results"])
    if failures:
        print(f"console pass: {total - len(failures)}/{total} routes clean")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"console pass: {total}/{total} routes render against the stack with no console error, on the palette")
    return 0


if __name__ == "__main__":
    sys.exit(main())
