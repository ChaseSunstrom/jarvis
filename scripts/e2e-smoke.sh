#!/usr/bin/env bash
#
# e2e-smoke.sh — prove a real Jarvis install works, on the real box.
#
# The pytest suite (jarvis-core/tests/test_e2e.py) proves the parts fit
# together in-process, with fake Wyoming and a fake Ollama. This script proves
# the OTHER half: that the thing actually starts on this machine, binds a port,
# authenticates, and does real work against whatever hardware is present.
#
# It boots a throwaway jarvis-core against a temporary config directory — your
# own config, database and tokens are never touched — waits for /healthz, mints
# a token, and drives the REST and websocket APIs.
#
# Checks that need a GPU, a model or the Wyoming containers are SKIPPED with a
# reason when those are not reachable. Nothing here ever blocks indefinitely:
# every network call is bounded, so a dead service costs you a timeout, not a
# hung terminal.
#
#   ./scripts/e2e-smoke.sh
#   LLM_URL=http://127.0.0.1:8080/v1 ./scripts/e2e-smoke.sh
#   ./scripts/e2e-smoke.sh --keep          # leave the temp config for a poke
#
# Exit status: 0 if nothing failed (skips are fine), 1 if any check failed,
# 2 if the environment is too broken to test at all.

# No `set -e`: every check runs, and the summary at the end is the point.
set -uo pipefail

# --- where things are -------------------------------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CORE_DIR="${REPO_DIR}/jarvis-core"

# --- knobs ------------------------------------------------------------------
# The model server, as `LLM_URL` names it: a base URL ending in /v1 for
# anything OpenAI-compatible (llama-swap, llama.cpp, vLLM, LiteLLM), or an
# Ollama base URL. `OLLAMA_URL` is still honoured for an install that predates
# the rename.
LLM_URL="${LLM_URL:-${OLLAMA_URL:-http://127.0.0.1:11434}}"
WYOMING_HOST="${WYOMING_HOST:-127.0.0.1}"
WYOMING_STT_PORT="${WYOMING_STT_PORT:-10300}"
WYOMING_TTS_PORT="${WYOMING_TTS_PORT:-10200}"
WYOMING_WAKE_PORT="${WYOMING_WAKE_PORT:-10400}"
PIPER_VOICE="${PIPER_VOICE:-en_GB-alan-medium}"

# Seconds to wait for the server to answer /healthz after launch.
BOOT_TIMEOUT="${BOOT_TIMEOUT:-45}"
# Seconds for an ordinary API call.
HTTP_TIMEOUT="${HTTP_TIMEOUT:-15}"
# Seconds for one LLM turn. A cold model genuinely takes a while; this is the
# point past which we call it broken rather than slow.
LLM_TIMEOUT="${LLM_TIMEOUT:-120}"
# Seconds for one TTS synthesis.
TTS_TIMEOUT="${TTS_TIMEOUT:-60}"
# Seconds to allow for a graceful SIGTERM shutdown. uvicorn drains in-flight
# requests first, so this has to exceed the slowest upstream timeout above.
SHUTDOWN_TIMEOUT="${SHUTDOWN_TIMEOUT:-70}"

KEEP_TEMP=0
PORT="${JARVIS_SMOKE_PORT:-}"

usage() {
    sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep) KEEP_TEMP=1; shift ;;
        --port) PORT="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

# --- pretty -----------------------------------------------------------------
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'
    YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
    BOLD=''; RED=''; GREEN=''; YELLOW=''; DIM=''; RESET=''
fi

say()  { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
die()  { printf '%s%s%s\n' "$RED" "$*" "$RESET" >&2; exit 2; }

# Milliseconds. bash 5 has EPOCHREALTIME; fall back to whole seconds.
now_ms() {
    local raw="${EPOCHREALTIME:-}"
    if [[ -z "$raw" ]]; then
        echo $(( $(date +%s) * 1000 ))
        return
    fi
    raw="${raw/,/.}"                       # some locales use a decimal comma
    local secs="${raw%.*}" frac="${raw#*.}"
    frac="${frac}000"
    echo $(( secs * 1000 + 10#${frac:0:3} ))
}

human_ms() {
    local ms="$1"
    if (( ms < 1000 )); then printf '%d ms' "$ms"
    else printf '%d.%02d s' $(( ms / 1000 )) $(( (ms % 1000) / 10 ))
    fi
}

# --- prerequisites ----------------------------------------------------------
command -v python3 >/dev/null 2>&1 || die "python3 is not on PATH."
command -v curl    >/dev/null 2>&1 || die "curl is not on PATH."
[[ -d "$CORE_DIR/jarvis" ]] || die "cannot find jarvis-core at ${CORE_DIR}."

python3 - <<'PY' || die "jarvis-core's dependencies are not installed (pip install -r jarvis-core/requirements.txt)."
import importlib.util
import sys

missing = [m for m in ("fastapi", "uvicorn", "httpx", "yaml", "jinja2")
           if importlib.util.find_spec(m) is None]
if missing:
    print("missing: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
PY

if [[ -z "$PORT" ]]; then
    PORT="$(python3 - <<'PY'
import socket
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
)"
fi
BASE="http://127.0.0.1:${PORT}"

# --- scratch ----------------------------------------------------------------
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-smoke.XXXXXX")" || die "cannot create a temp directory."
CONFIG_DIR="${TMP_ROOT}/config"
SERVER_LOG="${TMP_ROOT}/jarvis.log"
mkdir -p "$CONFIG_DIR"

SERVER_PID=""

cleanup() {
    local status=$?
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill -TERM "$SERVER_PID" 2>/dev/null
        for _ in $(seq 1 50); do
            kill -0 "$SERVER_PID" 2>/dev/null || break
            sleep 0.1
        done
        kill -0 "$SERVER_PID" 2>/dev/null && kill -KILL "$SERVER_PID" 2>/dev/null
        wait "$SERVER_PID" 2>/dev/null
    fi
    if (( KEEP_TEMP )); then
        say "${DIM}Left the scratch directory at ${TMP_ROOT}${RESET}"
    else
        rm -rf "$TMP_ROOT"
    fi
    exit "$status"
}
trap cleanup EXIT INT TERM

# --- the configuration under test -------------------------------------------
# Demo devices so there is always something real to switch, plus the voice and
# LLM blocks pointed at whatever this box is running.
cat > "${CONFIG_DIR}/configuration.yaml" <<YAML
jarvis:
  name: Jarvis Smoke Test
  latitude: 51.5072
  longitude: -0.1276
  time_zone: UTC
  log_level: info
  http:
    host: 127.0.0.1
    port: ${PORT}
  areas:
    - name: Living Room
    - name: Kitchen
    - name: Bedroom

recorder:
  db_file: smoke.db
  commit_interval: 1
  auto_purge: false

history:
  days: 1

demo:
  create_areas: true

input_boolean:
  smoke_flag:
    name: Smoke flag
    initial: "off"

voice:
  language: en
  stt:
    host: ${WYOMING_HOST}
    port: ${WYOMING_STT_PORT}
  tts:
    host: ${WYOMING_HOST}
    port: ${WYOMING_TTS_PORT}
    voice: ${PIPER_VOICE}
  wake:
    host: ${WYOMING_HOST}
    port: ${WYOMING_WAKE_PORT}
    model: hey_jarvis
  pipelines:
    - name: Jarvis
      voice: ${PIPER_VOICE}
      wake_word: hey_jarvis
      language: en

llm:
  url: ${LLM_URL}
  model: ${OLLAMA_MODEL:-qwen3:8b}
  max_tool_rounds: 3
  persona: "You are Jarvis. Answer in one short sentence."
  expose:
    domains: [light, switch, cover, climate, fan, media_player]
YAML

# ---------------------------------------------------------------------------
# check plumbing
# ---------------------------------------------------------------------------
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
RESULTS=()

#: A check function returns 0 to pass, 77 to skip, anything else to fail, and
#: echoes one line of detail either way.
SKIP=77

run_check() {
    local name="$1"; shift
    local start end elapsed output status
    start="$(now_ms)"
    output="$("$@" 2>&1)"
    status=$?
    end="$(now_ms)"
    elapsed=$(( end - start ))

    local label
    case "$status" in
        0)  label="PASS"; PASS_COUNT=$(( PASS_COUNT + 1 ))
            printf '  %s%-4s%s %-34s %s%s%s\n' \
                "$GREEN" "PASS" "$RESET" "$name" "$DIM" "$(human_ms "$elapsed")" "$RESET" ;;
        "$SKIP") label="SKIP"; SKIP_COUNT=$(( SKIP_COUNT + 1 ))
            printf '  %s%-4s%s %-34s %s%s%s\n' \
                "$YELLOW" "SKIP" "$RESET" "$name" "$DIM" "$(human_ms "$elapsed")" "$RESET" ;;
        *)  label="FAIL"; FAIL_COUNT=$(( FAIL_COUNT + 1 ))
            printf '  %s%-4s%s %-34s %s%s%s\n' \
                "$RED" "FAIL" "$RESET" "$name" "$DIM" "$(human_ms "$elapsed")" "$RESET" ;;
    esac
    [[ -n "$output" ]] && printf '       %s%s%s\n' "$DIM" "${output//$'\n'/$'\n'       }" "$RESET"
    RESULTS+=("${label}|${name}|${elapsed}")
    return 0
}

# --- small helpers ----------------------------------------------------------
# Passed to `python3 -c`, deliberately NOT a heredoc: a heredoc would become
# python's stdin and swallow the JSON we are piping in.
# (`read -d ''` always ends on EOF, so its non-zero status is expected.)
read -r -d '' JSON_GET_PY <<'PY' || true
import json, sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for part in sys.argv[1].split("."):
    if not part:
        continue
    if isinstance(data, list):
        try:
            data = data[int(part)]
        except (ValueError, IndexError):
            sys.exit(1)
    elif isinstance(data, dict):
        if part not in data:
            sys.exit(1)
        data = data[part]
    else:
        sys.exit(1)
print(data if isinstance(data, str) else json.dumps(data))
PY

#: Walk a dotted path through JSON on stdin. Non-zero if the path is absent.
json_get() {
    python3 -c "$JSON_GET_PY" "$1"
}

api() {  # api METHOD PATH [JSON-BODY] [TIMEOUT]
    local method="$1" path="$2" body="${3:-}" timeout="${4:-$HTTP_TIMEOUT}"
    if [[ -n "$body" ]]; then
        curl -sS --max-time "$timeout" -X "$method" \
            -H "Authorization: Bearer ${TOKEN}" \
            -H "Content-Type: application/json" \
            --data "$body" "${BASE}${path}"
    else
        curl -sS --max-time "$timeout" -X "$method" \
            -H "Authorization: Bearer ${TOKEN}" "${BASE}${path}"
    fi
}

tcp_open() {  # tcp_open HOST PORT [SECONDS]
    timeout "${3:-2}" bash -c "exec 3<>/dev/tcp/${1}/${2}" 2>/dev/null
}

# ---------------------------------------------------------------------------
# boot
# ---------------------------------------------------------------------------
say "${BOLD}Jarvis end-to-end smoke test${RESET}"
say "${DIM}core      ${CORE_DIR}"
say "config    ${CONFIG_DIR}"
say "listening 127.0.0.1:${PORT}"
say "model     ${LLM_URL}"
say "wyoming   ${WYOMING_HOST} stt:${WYOMING_STT_PORT} tts:${WYOMING_TTS_PORT} wake:${WYOMING_WAKE_PORT}${RESET}"
say ""

# A token is minted before the server starts, so it is in the store the server
# then loads. --create-token prints the secret on stdout and exits.
TOKEN="$(cd "$CORE_DIR" && python3 -m jarvis --config "$CONFIG_DIR" --create-token smoke 2>"${TMP_ROOT}/token.err" | tail -n 1 | tr -d '[:space:]')"
if [[ -z "$TOKEN" ]]; then
    warn "${RED}Could not mint an access token. jarvis said:${RESET}"
    sed 's/^/    /' "${TMP_ROOT}/token.err" >&2
    exit 2
fi

BOOT_START="$(now_ms)"
( cd "$CORE_DIR" && exec python3 -m jarvis --config "$CONFIG_DIR" \
    --host 127.0.0.1 --port "$PORT" ) >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

check_healthz() {
    local deadline=$(( SECONDS + BOOT_TIMEOUT )) body
    while (( SECONDS < deadline )); do
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            echo "the server exited during startup; last lines of its log:"
            tail -n 15 "$SERVER_LOG"
            return 1
        fi
        body="$(curl -sS --max-time 3 "${BASE}/healthz" 2>/dev/null)"
        if [[ -n "$body" ]] && [[ "$(printf '%s' "$body" | json_get status)" == "ok" ]]; then
            local entities
            entities="$(printf '%s' "$body" | json_get entities)"
            echo "version $(printf '%s' "$body" | json_get version), ${entities} entities"
            return 0
        fi
        sleep 0.25
    done
    echo "no healthy /healthz within ${BOOT_TIMEOUT}s; last lines of the log:"
    tail -n 15 "$SERVER_LOG"
    return 1
}

say "${BOLD}Boot${RESET}"
run_check "server starts and reports healthy" check_healthz

if (( FAIL_COUNT > 0 )); then
    say ""
    say "${RED}${BOLD}The server never came up — nothing else can be tested.${RESET}"
    say "Full log: ${SERVER_LOG}"
    (( KEEP_TEMP )) || say "(re-run with --keep to preserve it)"
    KEEP_TEMP=1
    exit 1
fi
BOOT_MS=$(( $(now_ms) - BOOT_START ))

# ---------------------------------------------------------------------------
# checks that need nothing but Jarvis
# ---------------------------------------------------------------------------
say ""
say "${BOLD}Core API${RESET} ${DIM}(no external services needed)${RESET}"

check_auth_required() {
    local code
    code="$(curl -sS --max-time "$HTTP_TIMEOUT" -o /dev/null -w '%{http_code}' "${BASE}/api/states")"
    if [[ "$code" != "401" ]]; then
        echo "GET /api/states without a token returned ${code}, expected 401"
        return 1
    fi
    code="$(curl -sS --max-time "$HTTP_TIMEOUT" -o /dev/null -w '%{http_code}' \
        -H "Authorization: Bearer not-a-real-token" "${BASE}/api/states")"
    if [[ "$code" != "401" ]]; then
        echo "a bogus token returned ${code}, expected 401"
        return 1
    fi
    echo "unauthenticated and bogus-token requests are both refused"
}

check_api_root() {
    local message
    message="$(api GET /api/ | json_get message)" || { echo "GET /api/ did not answer JSON"; return 1; }
    [[ "$message" == "API running." ]] || { echo "unexpected: ${message}"; return 1; }
    echo "authenticated, ${message}"
}

check_states() {
    local count
    count="$(api GET /api/states | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')" \
        || { echo "GET /api/states did not answer a JSON array"; return 1; }
    (( count > 0 )) || { echo "no entities exist"; return 1; }
    api GET /api/states/light.bed_light | json_get entity_id >/dev/null \
        || { echo "the demo light is missing (is demo: still in the config?)"; return 1; }
    echo "${count} entities, demo devices present"
}

check_service_call() {
    local state
    api POST /api/services/light/turn_on '{"entity_id":"light.bed_light","brightness":200}' >/dev/null \
        || { echo "light.turn_on failed"; return 1; }
    state="$(api GET /api/states/light.bed_light | json_get state)"
    [[ "$state" == "on" ]] || { echo "after turn_on the light is '${state}', expected 'on'"; return 1; }

    local brightness
    brightness="$(api GET /api/states/light.bed_light | json_get attributes.brightness)"
    [[ "$brightness" == "200" ]] || { echo "brightness is ${brightness}, expected 200"; return 1; }

    api POST /api/services/light/turn_off '{"entity_id":"light.bed_light"}' >/dev/null \
        || { echo "light.turn_off failed"; return 1; }
    state="$(api GET /api/states/light.bed_light | json_get state)"
    [[ "$state" == "off" ]] || { echo "after turn_off the light is '${state}', expected 'off'"; return 1; }
    echo "light.bed_light: off -> on (brightness 200) -> off"
}

check_input_helper() {
    api POST /api/services/input_boolean/turn_on '{"entity_id":"input_boolean.smoke_flag"}' >/dev/null \
        || { echo "input_boolean.turn_on failed"; return 1; }
    local state
    state="$(api GET /api/states/input_boolean.smoke_flag | json_get state)"
    [[ "$state" == "on" ]] || { echo "the helper is '${state}', expected 'on'"; return 1; }
    echo "input_boolean.smoke_flag flipped and persisted to .storage"
}

check_history() {
    # The recorder commits on its own interval; give it one.
    sleep 1.5
    local rows
    rows="$(api GET "/api/history/period?filter_entity_id=light.bed_light" \
        | python3 -c 'import json,sys
d = json.load(sys.stdin)
rows = d.get("light.bed_light", []) if isinstance(d, dict) else d
print(len(rows))')" || { echo "history/period did not answer JSON"; return 1; }
    (( rows > 0 )) || { echo "the recorder stored nothing for light.bed_light"; return 1; }
    echo "${rows} recorded rows read back from SQLite"
}

check_pipelines() {
    local names
    names="$(api GET /api/assist_pipeline/pipelines \
        | python3 -c 'import json,sys; print(", ".join(p["name"] for p in json.load(sys.stdin)["pipelines"]))')" \
        || { echo "the pipeline list is unreadable"; return 1; }
    [[ -n "$names" ]] || { echo "no voice pipelines are configured"; return 1; }
    echo "pipelines: ${names}"
}

check_websocket() {
    python3 - "$BASE" "$TOKEN" <<'PY'
import asyncio, json, sys

try:
    import websockets
except ImportError:
    print("the `websockets` package is not installed")
    sys.exit(77)

url = sys.argv[1].replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
token = sys.argv[2]


async def main() -> int:
    async with websockets.connect(url, open_timeout=10, close_timeout=5) as ws:
        challenge = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if challenge.get("type") != "auth_required":
            print(f"expected auth_required, got {challenge.get('type')!r}")
            return 1
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        ok = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if ok.get("type") != "auth_ok":
            print(f"authentication was refused: {ok}")
            return 1

        await ws.send(json.dumps({"id": 1, "type": "get_states"}))
        reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if not reply.get("success"):
            print(f"get_states failed: {reply}")
            return 1
        states = len(reply["result"])

        await ws.send(json.dumps({
            "id": 2, "type": "call_service", "domain": "light", "service": "turn_on",
            "target": {"entity_id": "light.ceiling_lights"},
        }))
        result = json.loads(await asyncio.wait_for(ws.recv(), 15))
        if not result.get("success"):
            print(f"call_service over the websocket failed: {result}")
            return 1
        print(f"handshake ok ({ok['ha_version']}), {states} states, call_service ok")
        return 0


try:
    sys.exit(asyncio.run(asyncio.wait_for(main(), 40)))
except asyncio.TimeoutError:
    print("the websocket did not answer within 40s")
    sys.exit(1)
except OSError as err:
    print(f"could not open the websocket: {err}")
    sys.exit(1)
PY
}

run_check "unauthenticated access is refused" check_auth_required
run_check "GET /api/ with a token"            check_api_root
run_check "entities exist"                    check_states
run_check "service call changes state"        check_service_call
run_check "input helper writes and persists"  check_input_helper
run_check "recorder + history round trip"     check_history
run_check "voice pipelines are configured"    check_pipelines
run_check "websocket handshake and command"   check_websocket

# ---------------------------------------------------------------------------
# checks that need the hardware-backed services
# ---------------------------------------------------------------------------
say ""
say "${BOLD}Local services${RESET} ${DIM}(skipped when not reachable)${RESET}"

check_model_server_reachable() {
    # `/v1/models` is the one endpoint every OpenAI-compatible server serves —
    # llama-swap, llama.cpp, vLLM, LM Studio, LiteLLM. Ollama serves it too,
    # alongside its own native model listing, so one probe covers both and
    # this script stops assuming which model server the house runs.
    local base models
    base="${LLM_URL%/}"
    [[ "$base" == */v1 ]] || base="${base}/v1"
    models="$(curl -sS --max-time 5 "${base}/models" 2>/dev/null \
        | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
ids = [m.get("id", "?") for m in d.get("data", [])]
print(", ".join(ids) or "none offered")' 2>/dev/null)"
    if [[ -z "$models" ]]; then
        echo "nothing answered ${base}/models — start your model server, or set LLM_URL. The conversation check is skipped."
        return "$SKIP"
    fi
    echo "models: ${models}"
}

check_conversation() {
    local base="${LLM_URL%/}"
    [[ "$base" == */v1 ]] || base="${base}/v1"
    if ! curl -sS --max-time 5 -o /dev/null "${base}/models" 2>/dev/null; then
        echo "no model server at ${base}"
        return "$SKIP"
    fi
    local body speech
    body="$(api POST /api/conversation/process \
        '{"text":"In one short sentence, what is the state of the bed light?"}' \
        "$LLM_TIMEOUT")"
    speech="$(printf '%s' "$body" | json_get response.speech.plain.speech)" || {
        echo "the reply was not a conversation response: ${body:0:200}"
        return 1
    }
    if [[ -z "${speech//[[:space:]]/}" ]]; then
        echo "the model answered with nothing at all"
        return 1
    fi
    echo "model said: \"${speech:0:120}\""
}

check_tts() {
    if ! tcp_open "$WYOMING_HOST" "$WYOMING_TTS_PORT"; then
        echo "no Wyoming TTS on ${WYOMING_HOST}:${WYOMING_TTS_PORT} — piper is not running."
        return "$SKIP"
    fi
    local url
    url="$(api POST "/api/services/voice/say?return_response=true" \
        '{"text":"Systems nominal, Sir."}' "$TTS_TIMEOUT" | json_get service_response.url)" || {
        echo "voice.say returned no audio url (is PIPER_VOICE=${PIPER_VOICE} the voice piper loaded?)"
        return 1
    }
    local wav="${TMP_ROOT}/say.wav"
    curl -sS --max-time "$TTS_TIMEOUT" -o "$wav" "${BASE}${url}" || {
        echo "could not fetch ${url}"; return 1;
    }
    local size header
    size="$(wc -c < "$wav" | tr -d ' ')"
    header="$(head -c 4 "$wav")"
    [[ "$header" == "RIFF" ]] || { echo "the served audio is not a WAV file"; return 1; }
    python3 - "$wav" <<'PY' || return 1
import sys, wave
with wave.open(sys.argv[1]) as handle:
    if handle.getnframes() <= 0:
        print("the WAV has no audio frames")
        raise SystemExit(1)
PY
    echo "piper spoke ${size} bytes of playable WAV"
}

check_stt_reachable() {
    if ! tcp_open "$WYOMING_HOST" "$WYOMING_STT_PORT"; then
        echo "no Wyoming STT on ${WYOMING_HOST}:${WYOMING_STT_PORT} — whisper is not running."
        return "$SKIP"
    fi
    echo "reachable on ${WYOMING_HOST}:${WYOMING_STT_PORT} (transcription itself needs real audio — see docs/verification.md)"
}

check_wake_reachable() {
    if ! tcp_open "$WYOMING_HOST" "$WYOMING_WAKE_PORT"; then
        echo "no Wyoming wake word on ${WYOMING_HOST}:${WYOMING_WAKE_PORT} — openWakeWord is not running."
        return "$SKIP"
    fi
    echo "reachable on ${WYOMING_HOST}:${WYOMING_WAKE_PORT} (detection needs a real utterance — see docs/verification.md)"
}

run_check "Ollama is reachable"           check_model_server_reachable
run_check "a real LLM turn answers"       check_conversation
run_check "Wyoming STT is reachable"      check_stt_reachable
run_check "Wyoming wake word is reachable" check_wake_reachable
run_check "Wyoming TTS synthesises a WAV" check_tts

# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------
say ""
say "${BOLD}Shutdown${RESET}"

check_clean_shutdown() {
    kill -TERM "$SERVER_PID" 2>/dev/null || { echo "the server was already gone"; return 1; }
    local deadline=$(( SECONDS + SHUTDOWN_TIMEOUT ))
    while (( SECONDS < deadline )); do
        kill -0 "$SERVER_PID" 2>/dev/null || break
        sleep 0.1
    done
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "still running ${SHUTDOWN_TIMEOUT}s after SIGTERM."
        echo "uvicorn drains in-flight requests before it exits, so an earlier check"
        echo "that timed out against a hung service can hold this open for as long as"
        echo "that service's own timeout (voice: tts: timeout, default 60s)."
        return 1
    fi
    wait "$SERVER_PID" 2>/dev/null
    SERVER_PID=""
    if grep -q "Traceback (most recent call last)" "$SERVER_LOG"; then
        echo "the log contains a traceback:"
        grep -A 6 "Traceback (most recent call last)" "$SERVER_LOG" | head -n 12
        return 1
    fi
    echo "exited on SIGTERM with no tracebacks in the log"
}

run_check "SIGTERM shuts down cleanly" check_clean_shutdown

# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------
TOTAL=$(( PASS_COUNT + FAIL_COUNT + SKIP_COUNT ))
say ""
say "${BOLD}Summary${RESET}"
say "  boot to healthy      $(human_ms "$BOOT_MS")"
say "  checks run           ${TOTAL}"
say "  ${GREEN}passed               ${PASS_COUNT}${RESET}"
if (( SKIP_COUNT > 0 )); then
    say "  ${YELLOW}skipped              ${SKIP_COUNT}${RESET} ${DIM}(a service was not running — not a failure)${RESET}"
fi
if (( FAIL_COUNT > 0 )); then
    say "  ${RED}failed               ${FAIL_COUNT}${RESET}"
fi
say ""

if (( FAIL_COUNT > 0 )); then
    say "${RED}${BOLD}SMOKE TEST FAILED${RESET} — ${FAIL_COUNT} of ${TOTAL} checks did not pass."
    say "Server log kept at ${SERVER_LOG}"
    KEEP_TEMP=1
    exit 1
fi

if (( SKIP_COUNT > 0 )); then
    say "${GREEN}${BOLD}SMOKE TEST PASSED${RESET} ${DIM}(${SKIP_COUNT} check(s) skipped — see above)${RESET}"
else
    say "${GREEN}${BOLD}SMOKE TEST PASSED${RESET} — every check ran and passed."
fi
say "${DIM}What this does NOT cover: microphone capture, wake-word accuracy, the"
say "Android app, the browser HUD and cross-device routing. docs/verification.md"
say "says which of those are proven where, and how to check the rest.${RESET}"
exit 0
