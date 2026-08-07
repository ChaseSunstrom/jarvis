#!/usr/bin/env bash
# P9: apply the Jarvis ufw policy. Idempotent. Run as root on the server.
# Zero cloud at runtime — the only intended outbound path is SearXNG's
# upstream fetch (SearXNG makes that itself; nothing here opens it).
set -euo pipefail

LAN="${JARVIS_LAN:-192.168.2.0/24}"
DRY="${DRY_RUN:-0}"

run() {
  echo "+ $*"
  [ "$DRY" = "1" ] || "$@"
}

if ! command -v ufw >/dev/null 2>&1; then
  if [ "$DRY" = "1" ]; then
    echo "(dry-run) ufw not installed here; showing the commands that would run:"
  else
    echo "ufw not installed. apt-get install ufw" >&2
    exit 1
  fi
fi

run ufw --force default deny incoming
run ufw --force default allow outgoing        # egress narrowed per-service below/notes
run ufw allow in on wg0

# Home Assistant + Ollama + Wyoming + SearXNG + HUD, LAN only.
run ufw allow from "$LAN" to any port 8123 proto tcp     # Home Assistant
run ufw allow from "$LAN" to any port 11434 proto tcp    # Ollama
run ufw allow from "$LAN" to any port 10300 proto tcp    # Wyoming STT
run ufw allow from "$LAN" to any port 10200 proto tcp    # Wyoming TTS (Piper)
run ufw allow from "$LAN" to any port 10400 proto tcp    # Wyoming wake (OWW)
run ufw allow from "$LAN" to any port 8081 proto tcp     # SearXNG
run ufw allow from "$LAN" to any port 8199 proto tcp     # jarvis-web HUD

# Orchestrator: reachable from the HA host ONLY (default: this host).
HA_HOST="${JARVIS_HA_HOST:-127.0.0.1}"
run ufw allow from "$HA_HOST" to any port 8188 proto tcp # jarvis-orchestrator
# jarvis-sandbox has network_mode: none — nothing to allow, nothing to deny.

run ufw --force enable
run ufw reload
echo
echo "Applied. Review with: ufw status verbose"
echo "NOTE: default outgoing is 'allow' so HF model downloads work during"
echo "setup. AFTER first run, tighten egress (see docs/security.md §egress):"
echo "  ufw default deny outgoing"
echo "  ufw allow out to <SEARXNG_UPSTREAM_DNS/HTTPS as needed>"
echo "  ufw allow out on wg0"
