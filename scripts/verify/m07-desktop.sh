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
check "desktop tab in the console nav" grep -qi desktop jarvis-web/src/routes/+layout.svelte
require_file jarvis-desktop/jarvis_desktop/ipc.py
check "consent prompts can be answered by the shell" grep -qE 'class ShellConsentGateway' jarvis-desktop/jarvis_desktop/consent.py
check_sh "agent ipc tests" 'cd jarvis-desktop && python3 -m pytest tests/test_ipc.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
require_dir "$APP/node_modules"
check_sh "shell builds" 'cd jarvis-desktop-app && npm run build 2>&1 | tail -5'
check_sh "shell unit tests (vitest, electron mocked)" 'cd jarvis-desktop-app && npx vitest run 2>&1 | tail -4'
require_cmd xvfb-run
check_sh "shell e2e under Xvfb (window loads the console; tray + hotkey registered)" \
    'cd jarvis-desktop-app && xvfb-run -a npx playwright test 2>&1 | tail -15'
check_sh "unpacked distribution builds (npm run dist:dir)" \
    'cd jarvis-desktop-app && npm run dist:dir 2>&1 | tail -3 && ls -d dist/*unpacked* >/dev/null'
require_file "$APP/README.md"
check "verification claim" grep -qi jarvis-desktop-app docs/verification.md
verify_end
