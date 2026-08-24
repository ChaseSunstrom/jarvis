#!/usr/bin/env python3
"""Every web screen declares itself and handles the four states.

Static half of the loading/empty/error/offline check (the Playwright spec
`e2e/states.spec.ts` is the dynamic half). Requires:

  * `jarvis-web/src/lib/screens.ts` — the screen manifest: one entry per
    routed page (`path`, `name`), which the e2e spec iterates;
  * every `+page.svelte` under `src/routes` (except `api/`, `healthz`,
    `styleguide`) is in the manifest, and every manifest path has a page;
  * every such page uses `<ScreenState` from `$lib/ui`, the one component
    that renders loading / empty / error / offline, so a screen cannot forget
    a state by not writing it;
  * `src/routes/+error.svelte` exists (a thrown error is a state too);
  * something observes `navigator.onLine` / the `offline` event.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "jarvis-web" / "src"
SKIP = ("api/", "healthz", "styleguide")
problems: list[str] = []

manifest = WEB / "lib" / "screens.ts"
declared: set[str] = set()
if manifest.is_file():
    declared = set(re.findall(r"path:\s*['\"]([^'\"]+)['\"]", manifest.read_text()))
    if not declared:
        problems.append(f"{manifest}: no `path: '...'` entries found")
else:
    problems.append(f"missing screen manifest: {manifest}")

pages = sorted(WEB.glob("routes/**/+page.svelte"))
routed: set[str] = set()
for page in pages:
    rel = page.relative_to(WEB / "routes").as_posix()
    if any(s in rel for s in SKIP):
        continue
    path = "/" + rel[: -len("+page.svelte")].rstrip("/")
    routed.add(path)
    text = page.read_text()
    if "<ScreenState" not in text:
        problems.append(f"{page.relative_to(ROOT)}: does not use <ScreenState> from $lib/ui")
    if path not in declared:
        problems.append(f"{page.relative_to(ROOT)}: route {path} is not in src/lib/screens.ts")

for path in sorted(declared - routed):
    problems.append(f"screens.ts declares {path} but no +page.svelte exists for it")

if not (WEB / "routes" / "+error.svelte").is_file():
    problems.append("missing jarvis-web/src/routes/+error.svelte")

offline_seen = any(
    ("navigator.onLine" in p.read_text() or "'offline'" in p.read_text())
    for p in (WEB / "lib").glob("*.ts")
)
if not offline_seen:
    problems.append("nothing under src/lib observes navigator.onLine / the offline event")

if problems:
    print("\n".join(problems))
    print(f"\n{len(problems)} problem(s) across {len(routed)} routed page(s)")
    sys.exit(1)
print(f"{len(routed)} screens declared, every one uses <ScreenState>; +error.svelte and offline detection present")
