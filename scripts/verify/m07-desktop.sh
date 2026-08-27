#!/usr/bin/env bash
# M07 — a desktop app: the console on the design system inside a native shell
# with tray, notifications and a push-to-talk hotkey, backed by the existing
# device agent; verified headless (vitest, Playwright + Electron under Xvfb).
source "$(dirname "$0")/lib.sh"
verify_begin "M07" "desktop app: console shell, tray, notifications, push-to-talk"
use_venv
APP=jarvis-desktop-app

require_file "$APP/package.json"
check "Electron shell" grep -q '"electron"' "$APP/package.json"
check_sh "main process creates a Tray" 'grep -rqE "\bTray\b" jarvis-desktop-app/src/main'
check_sh "main process posts Notifications" 'grep -rqE "\bNotification\b" jarvis-desktop-app/src/main'
check_sh "main process registers a global push-to-talk shortcut" 'grep -rq globalShortcut jarvis-desktop-app/src/main'
check_sh "push-to-talk reaches the renderer over IPC" 'grep -rqiE "push.?to.?talk|ptt" jarvis-desktop-app/src'
require_generated "$APP/src/renderer/tokens.css"
check "token lint: the shell is clean" python3 scripts/verify/token_lint.py --require-clean jarvis-desktop-app/src
check_sh "the shell serves the console build — parity by construction" 'grep -rqE "jarvis-web|build/index\.js|loadURL" jarvis-desktop-app/src/main'
# The layout builds its tab strip from `screens.ts` now, so grepping it for
# the word found nothing — and desktop had not gone anywhere, it had become
# a section of SETTINGS.
# The desktop page moved into SETTINGS › Console with M54 (its two panels —
# this window, paired computers — live there) and its old address redirects,
# so what the console has to reach is Console, and the old address has to
# still answer.
check "the console reaches the desktop panels (SETTINGS › Console) and the old address still answers" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/screens.ts").read_text()
assert "/settings/console" in src, "no Console section in screens.ts"
redirect = Path("jarvis-web/src/routes/settings/desktop/+page.ts").read_text()
assert "/settings/console" in redirect and "redirect" in redirect, "/settings/desktop no longer answers"
print("desktop: SETTINGS > Console; /settings/desktop redirects there")
'
require_file jarvis-desktop/jarvis_desktop/ipc.py
check "consent prompts can be answered by the shell" grep -qE 'class ShellConsentGateway' jarvis-desktop/jarvis_desktop/consent.py
check_pytest "agent ipc tests" 'cd jarvis-desktop && python3 -m pytest tests/test_ipc.py -q --timeout=120 --timeout-method=signal'
require_dir "$APP/node_modules"
check_sh "shell builds" 'cd jarvis-desktop-app && npm run build 2>&1 | tail -5'
check_sh "shell unit tests (vitest, electron mocked)" 'cd jarvis-desktop-app && npx vitest run 2>&1 | tail -4'
# `Xvfb`, not `xvfb-run`: the wrapper shells out to `xauth`, which is a
# separate package this host does not have and cannot install (no root).
# `jarvis-desktop-app/tools/xvfb.sh` starts Xvfb itself and puts Electron's
# libraries — unpacked under $HOME by `tools/electron-runtime.sh` — on the
# library path, which is the same "nothing system-wide" constraint the JDK and
# the Android SDK are installed under.
require_cmd Xvfb
require_exec jarvis-desktop-app/tools/xvfb.sh
require_exec jarvis-desktop-app/tools/electron-runtime.sh
check_sh "shell e2e under Xvfb (window loads the console; preload surface; hotkey registered)" \
    'cd jarvis-desktop-app && bash tools/xvfb.sh npx playwright test 2>&1 | tail -15'
check_sh "unpacked distribution builds (npm run dist:dir)" \
    'cd jarvis-desktop-app && npm run dist:dir 2>&1 | tail -3 && ls -d dist-app/*unpacked* >/dev/null'
require_file "$APP/README.md"
check "verification claim" grep -qi jarvis-desktop-app docs/verification.md
# No live scenarios of its own — this milestone does not add a capability
# anybody talks to. What it must not do is break the ones that exist, so a
# named smoke subset runs: house-light-on, chat-context-retention, lock-needs-a-human.
check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention,lock-needs-a-human bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
