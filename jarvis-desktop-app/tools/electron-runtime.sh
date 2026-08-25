#!/usr/bin/env bash
# Electron's shared libraries, under $HOME, with no root.
#
#   bash jarvis-desktop-app/tools/electron-runtime.sh
#
# Electron is a Chromium, and Chromium needs GTK, NSS, ALSA and a dozen other
# system libraries. This host has no sudo and none of them; `apt-get download`
# needs neither, and `dpkg -x` unpacks into a prefix of our choosing. The
# result is a directory to put on `LD_LIBRARY_PATH`, which is what
# `tools/xvfb.sh` does.
#
# Idempotent, and about 120 MB the first time. It writes nothing outside
# $ELECTRON_RUNTIME (default ~/.local/electron-runtime).
set -euo pipefail

PREFIX="${ELECTRON_RUNTIME:-$HOME/.local/electron-runtime}"
STAMP="$PREFIX/.complete"

# The closure Electron 33 actually asks for on Debian 12, discovered by running
# it and reading `error while loading shared libraries` until it started. Kept
# as a list rather than `apt-get install -d` because the point is to unpack
# them somewhere unusual, and because a surprise addition to this list should
# be a diff somebody reads.
PACKAGES=(
    libgtk-3-0 libgdk-pixbuf-2.0-0 libpango-1.0-0 libpangocairo-1.0-0
    libcairo2 libcairo-gobject2 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0
    libnss3 libnspr4 libnotify4 libxss1 libxtst6 libxdamage1 libxfixes3
    libxcomposite1 libxrandr2 libxkbcommon0 libxcb-dri3-0 libgbm1 libdrm2
    libasound2 libcups2 libepoxy0 libwayland-client0 libwayland-cursor0
    libwayland-egl1 libharfbuzz0b libfribidi0 libthai0 libdatrie1
    libgraphite2-3 libavahi-client3 libavahi-common3 libcolord2 liblcms2-2
    libpangoft2-1.0-0 libfontconfig1 libfreetype6 libpixman-1-0 libxinerama1
    libxcursor1 libxi6 libxrender1 libxext6 libx11-xcb1 libxcb-shm0
    libgtk-3-common libgdk-pixbuf2.0-common
)

if [ -f "$STAMP" ]; then
    echo "electron runtime already at $PREFIX"
else
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    mkdir -p "$PREFIX"
    (
        cd "$tmp"
        # One at a time: a package that has been renamed upstream should name
        # itself in the failure rather than taking the whole batch with it.
        for package in "${PACKAGES[@]}"; do
            apt-get download "$package" >/dev/null 2>&1 || echo "skipped $package (not available)"
        done
        for deb in *.deb; do
            [ -e "$deb" ] || continue
            dpkg -x "$deb" "$PREFIX"
        done
    )
    touch "$STAMP"
fi

# Everything unpacked, in one path. Printed rather than exported, because a
# script that sets a variable in a subshell has done nothing.
printf '%s/usr/lib/x86_64-linux-gnu:%s/lib/x86_64-linux-gnu\n' "$PREFIX" "$PREFIX"
