#!/usr/bin/env bash
# M06 — an InfluxDB source adapter so homelab/GPU series can be graphed.
# Proven offline against a fake InfluxDB (v1 InfluxQL and v2 Flux); the live
# claim is Scripted (scripts/check-influx.py) in docs/verification.md.
source "$(dirname "$0")/lib.sh"
verify_begin "M06" "InfluxDB data-source adapter"
use_venv
SRC=jarvis-core/jarvis/metrics/sources/influx.py

require_file "$SRC"
check "InfluxQL (1.x, 2.x compat) supported" grep -q '/query' "$SRC"
check "Flux (2.x) supported" grep -q '/api/v2/query' "$SRC"
check "server generation detected from /health or /ping" grep -qE '/health|/ping' "$SRC"
check "token travels in the Authorization header" grep -q Authorization "$SRC"
check ".env.example documents INFLUX_URL / INFLUX_TOKEN" grep -qE '^#?\s*INFLUX_(URL|TOKEN)' jarvis-core/.env.example
check "configuration is documented" grep -rqi influx jarvis-core/docs/
check "verification claim with a reproducible command" grep -qi influx docs/verification.md
require_file scripts/check-influx.py
require_file jarvis-core/tests/test_metrics_influx.py
check_sh "adapter tests against a fake InfluxDB (v1 + v2)" \
    'cd jarvis-core && python3 -m pytest tests/test_metrics_influx.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
check "widget editor can pick an Influx source" grep -rqi influx jarvis-web/src/lib/dashboards
check "an example homelab/GPU dashboard ships" grep -rqi gpu jarvis-core/config/dashboards/
check "mock backend offers an influx source" grep -qi influx tests/web/mock-ha.mjs
verify_end
