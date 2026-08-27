#!/usr/bin/env bash
# Run a command with a display and Electron's libraries, needing no root.
#
#   bash tools/xvfb.sh npx playwright test
#
# Two host problems, one script:
#
# * `xvfb-run` shells out to `xauth`, which is a separate package this host
#   does not have and cannot install. Starting `Xvfb` directly needs neither.
# * Electron is a Chromium and wants GTK, NSS, ALSA and a dozen more. They are
#   unpacked under $HOME by `tools/electron-runtime.sh`, and this is where they
#   go on the library path.
#
# Both are the same constraint the rest of this project builds under: nothing
# is installed system-wide, and everything says so out loud.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
RUNTIME="${ELECTRON_RUNTIME:-$HOME/.local/electron-runtime}"
if [ ! -f "$RUNTIME/.complete" ]; then
    bash "$HERE/electron-runtime.sh" >/dev/null
fi
export LD_LIBRARY_PATH="$RUNTIME/usr/lib/x86_64-linux-gnu:$RUNTIME/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
# GTK looks for its own data (icon theme, immodules) beside the libraries.
export XDG_DATA_DIRS="$RUNTIME/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

for n in $(seq 90 120); do
    if [ ! -e "/tmp/.X11-unix/X$n" ]; then DISPLAY_NUM=$n; break; fi
done
: "${DISPLAY_NUM:?no free display number between 90 and 120}"

Xvfb ":$DISPLAY_NUM" -screen 0 1280x1024x24 -nolisten tcp >/dev/null 2>&1 &
XVFB_PID=$!
trap 'kill "$XVFB_PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
    [ -e "/tmp/.X11-unix/X$DISPLAY_NUM" ] && break
    sleep 0.1
done

DISPLAY=":$DISPLAY_NUM" "$@"
