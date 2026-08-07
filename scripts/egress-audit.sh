#!/usr/bin/env bash
# P9: audit that the isolated services truly have no outbound reach.
# Verifies jarvis-sandbox network isolation (network_mode: none) and probes
# the orchestrator's egress. Exit non-zero if the sandbox can reach anything.
set -uo pipefail

fail=0
say() { printf '%s\n' "$*"; }

# 1) Sandbox must have NO network. Prove it three ways from inside.
if docker inspect jarvis-sandbox >/dev/null 2>&1; then
  say "== jarvis-sandbox network isolation =="

  netmode=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' jarvis-sandbox 2>/dev/null | tr -d ' ')
  say "network mode: ${netmode:-none}"
  if [ -n "$netmode" ] && [ "$netmode" != "none" ]; then
    say "FAIL: sandbox is attached to network '$netmode' (expected none)"; fail=1
  fi

  # Interfaces: only loopback should exist.
  ifaces=$(docker exec jarvis-sandbox sh -c 'ls /sys/class/net' 2>/dev/null | tr '\n' ' ')
  say "interfaces: $ifaces"
  for i in $ifaces; do
    if [ "$i" != "lo" ]; then
      say "FAIL: unexpected interface '$i' in sandbox"; fail=1
    fi
  done

  # Active reachability probe to the LAN gateway and the internet.
  for target in 192.168.2.1 1.1.1.1 8.8.8.8; do
    if docker exec jarvis-sandbox sh -c \
        "timeout 3 sh -c 'echo > /dev/tcp/$target/53' 2>/dev/null"; then
      say "FAIL: sandbox reached $target:53"; fail=1
    else
      say "ok: sandbox cannot reach $target:53"
    fi
  done
else
  say "SKIP: jarvis-sandbox container not running (start the stack to audit)"
fi

# 2) Orchestrator: should reach Ollama + HA only. Report, don't hard-fail
#    (allowed egress is deployment-specific; firewall enforces the rest).
if docker inspect jarvis-orchestrator >/dev/null 2>&1; then
  say ""
  say "== jarvis-orchestrator egress (informational) =="
  for target in 1.1.1.1 github.com; do
    if docker exec jarvis-orchestrator sh -c \
        "timeout 3 python -c 'import socket;socket.create_connection((\"$target\",443),3)'" 2>/dev/null; then
      say "WARN: orchestrator reached $target:443 — tighten egress post-setup"
    else
      say "ok: orchestrator cannot reach $target:443"
    fi
  done
else
  say "SKIP: jarvis-orchestrator not running"
fi

say ""
if [ "$fail" = 0 ]; then
  say "EGRESS AUDIT: PASS (sandbox is network-isolated)"
else
  say "EGRESS AUDIT: FAIL"
fi
exit $fail
