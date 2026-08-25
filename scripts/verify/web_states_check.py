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

SECTIONS = WEB / "lib" / "sections"


def rendered_source(page: Path) -> str:
    """The markup a route actually shows.

    Since the consolidation (M48) most routes are two lines — an import and a
    component from `lib/sections/`. Reading only the route file said every one
    of them had lost its states, which was the opposite of true: the states
    moved into the section, whole, and the route is a mount point.
    """
    text = page.read_text()
    for name in re.findall(r"from\s+['\"]\$lib/sections/([A-Za-z]+)\.svelte['\"]", text):
        section = SECTIONS / f"{name}.svelte"
        if section.is_file():
            text += "\n" + section.read_text()
    return text


pages = sorted(WEB.glob("routes/**/+page.svelte"))
routed: set[str] = set()
for page in pages:
    rel = page.relative_to(WEB / "routes").as_posix()
    if any(s in rel for s in SKIP):
        continue
    path = "/" + rel[: -len("+page.svelte")].rstrip("/")
    routed.add(path)
    text = rendered_source(page)
    if "<ScreenState" not in text:
        problems.append(f"{page.relative_to(ROOT)}: does not use <ScreenState> from $lib/ui")
    if path not in declared:
        problems.append(f"{page.relative_to(ROOT)}: route {path} is not in src/lib/screens.ts")

# A destination is a layout and a redirect to its first section, so it has no
# `+page.svelte` of its own — and must not: two pages rendering the same thing
# are two pages that drift, and the one nobody opens drifts first.
for path in sorted(declared - routed):
    folder = WEB / "routes" / path.lstrip("/")
    served_by_redirect = (folder / "+page.ts").is_file() and (folder / "+layout.svelte").is_file()
    if not served_by_redirect:
        problems.append(
            f"screens.ts declares {path} and nothing serves it — no +page.svelte, "
            "and no +layout.svelte with a +page.ts redirect either"
        )

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
