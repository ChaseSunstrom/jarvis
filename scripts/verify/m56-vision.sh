#!/usr/bin/env bash
# M56 — cameras and local vision. The `vision` integration speaks the OpenAI
# wire to a GGUF VLM on the model server, go2rtc turns any camera into a
# snapshot URL behind `--profile cameras`, Frigate's events can land as
# moments, and the rig owns a camera of its own. Consent, fencing and the
# audit trail are the part that must NOT have changed — so the refusal test
# is the one this script names first.
source "$(dirname "$0")/lib.sh"
verify_begin "M56" "cameras and local vision"
use_venv

VISION=jarvis-core/jarvis/integrations/vision
require_file "$VISION/analyze.py"
require_file "$VISION/frigate.py"
require_file jarvis-core/tests/test_vision_openai.py
require_file tests/contracts/vision_events.json

# --- the wire ---------------------------------------------------------------
check "the OpenAI wire exists: a look is an image_url content part" \
    grep -q '"image_url"' "$VISION/analyze.py"
check "a frame is only ever sent as base64, never as a URL the model host would fetch" \
    grep -q 'data:image/jpeg;base64,' "$VISION/analyze.py"
check "a gateway or llama-swap url defaults to openai; a bare Ollama url stays ollama" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.integrations.vision.analyze import VisionConfig
gateway = VisionConfig.from_config({"url": "http://127.0.0.1:4000/v1", "model": "house-vision"})
assert gateway.backend == "openai", gateway.backend
swap = VisionConfig.from_config({"url": "http://models.lan:8080/v1"})
assert swap.backend == "openai", swap.backend
ollama = VisionConfig.from_config({"ollama_url": "http://127.0.0.1:11434"})
assert ollama.backend == "ollama", ollama.backend
print(f"gateway -> {gateway.backend}, bare 11434 -> {ollama.backend}")
'
check "the api key is read from the env name the config gives, never from the tree" python3 -c '
import os, sys; sys.path.insert(0, "jarvis-core")
from jarvis.integrations.vision.analyze import VisionConfig
os.environ["M56_PROBE_KEY"] = "sk-probe"
cfg = VisionConfig.from_config({"url": "http://127.0.0.1:4000/v1", "api_key_env": "M56_PROBE_KEY"})
assert cfg.api_key == "sk-probe", cfg.api_key
print("api_key_env resolves at setup")
'

PYTEST='cd jarvis-core && python3 -m pytest -q --timeout=120 --timeout-method=signal'
check_sh "a refusal on the OpenAI path sends nothing to the camera and nothing to the model" \
    "$PYTEST tests/test_vision_openai.py -k 'refuse or denied or never' 2>&1 | tail -2"
check_sh "the request has exactly the shape llama.cpp's server reads" \
    "$PYTEST tests/test_vision_openai.py -k 'payload' 2>&1 | tail -2"
check_sh "a 4xx or 5xx from the model server is a clean could-not-look record" \
    "$PYTEST tests/test_vision_openai.py -k 'error or unreachable' 2>&1 | tail -2"
check_sh "a public model url is refused by vision as it is by llm" \
    "$PYTEST tests/test_vision_openai.py -k 'local' 2>&1 | tail -2"
check_sh "the events carry what the voice tab draws (the contract)" \
    "$PYTEST tests/test_vision_openai.py -k 'contract or events' 2>&1 | tail -2"
check_sh "Frigate events become moments, one per event id" \
    "$PYTEST tests/test_vision_openai.py -k 'frigate' 2>&1 | tail -2"
check_sh "the whole vision suite, both wires" \
    "$PYTEST tests/test_vision.py tests/test_vision_openai.py 2>&1 | tail -2"

# --- ingest -------------------------------------------------------------------
require_file jarvis-core/go2rtc/go2rtc.yaml
check "go2rtc is behind --profile cameras, pinned, on host networking with its API on loopback" python3 -c '
import yaml
compose = yaml.safe_load(open("jarvis-core/docker-compose.yml"))
svc = compose["services"]["go2rtc"]
assert svc["profiles"] == ["cameras"], svc.get("profiles")
assert svc["image"] == "alexxit/go2rtc:1.9.14", svc["image"]
assert svc["network_mode"] == "host"
assert "./go2rtc/go2rtc.yaml:/config/go2rtc.yaml:ro" in svc["volumes"]
cfg = yaml.safe_load(open("jarvis-core/go2rtc/go2rtc.yaml"))
assert cfg["api"]["listen"].startswith("127.0.0.1:"), cfg["api"]
assert cfg["rtsp"]["listen"].startswith("127.0.0.1:"), cfg["rtsp"]
for name in ("front_door", "workshop", "garden"):
    assert name in cfg["streams"], f"the example {name} stream is missing"
print("go2rtc 1.9.14, profile cameras, api on " + cfg["api"]["listen"])
'
check "platform: go2rtc resolves to the snapshot endpoint" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.integrations.vision.camera import CameraConfig
cam = CameraConfig.from_config({"name": "Kitchen", "platform": "go2rtc", "stream": "kitchen"})
assert cam.url == "http://127.0.0.1:1984/api/frame.jpeg?src=kitchen&w=1280", cam.url
print(cam.url)
'
check_sh "test_packaging agrees: compose, config and .env.example" \
    "$PYTEST tests/test_packaging.py 2>&1 | tail -2"

# --- the rig's own camera -----------------------------------------------------
require_file testing/live/fixtures/handbook/camera/kitchen.jpg
check "the fixture site serves /camera/kitchen.jpg as a real JPEG" python3 -c '
import sys, urllib.request; sys.path.insert(0, ".")
from testing.live.fixture_site import Site
with Site() as site:
    with urllib.request.urlopen(site.url + "/camera/kitchen.jpg", timeout=5) as r:
        body = r.read(); ctype = r.headers.get("content-type", "")
assert ctype.startswith("image/jpeg"), ctype
assert body[:2] == b"\xff\xd8" and body[-2:] == b"\xff\xd9", "not a JPEG"
assert 2000 < len(body) < 200000, len(body)
print(f"{len(body)} bytes, {ctype}")
'
check "the scenario parses, is gated on M56, runs on the fixture ground and asserts vision" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.scenario import load_scenario
s = load_scenario("testing/live/scenarios/vision-look-fixture.yaml")
assert s.capability == "vision" and s.gated_on == "M56" and s.ground == "fixture", (s.capability, s.gated_on, s.ground)
assert "VISION_MODEL" in s.intent, "the intent must say it needs a served VLM"
turn = s.turns[0]
assert turn.expect.get("capability") == "vision"
assert "mug" in turn.expect.get("reply_means", "")
print(f"{s.name}: {s.variants}")
'
check "a camera look is routed as vision, and the table names real tools" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.capability import TOOL_CAPABILITY, capability_of
assert TOOL_CAPABILITY["look_at_camera"] == "vision"
assert capability_of([], [], ["look_at_camera"], "") == "vision"
print("look_at_camera -> vision")
'
check "the harness writes a vision block only when it is given a camera and a model" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.harness.harness import build_config
kw = dict(port=1, host="127.0.0.1", ollama_url="http://127.0.0.1:4000/v1", stt_port=2, tts_port=3, wake_port=4)
assert "vision:" not in build_config(**kw)
text = build_config(**kw, vision_camera_url="http://127.0.0.2:8901/camera/kitchen.jpg", vision_model="house-vision")
assert "vision:" in text and "consent: always" in text and "house-vision" in text, text
print("vision block present when asked for")
'
check_sh "the rig's own tests" \
    'python3 -m pytest testing/live/tests -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'

# --- hygiene ------------------------------------------------------------------
check "ruff is clean on everything this milestone touched" \
    python3 -m ruff check "$VISION" jarvis-core/jarvis/llm/openai_compat.py \
        jarvis-core/tests/test_vision.py jarvis-core/tests/test_vision_openai.py \
        testing/live testing/harness
# --- live ---------------------------------------------------------------------
# The integrator's line. It needs a multimodal model on the model server —
# `VISION_MODEL` names it, e.g. a llama-swap entry started with `--mmproj` —
# and it runs on the harness ground (this repository's own jarvis-core and
# fixture web), so it recreates no container and borrows no browser. Without
# the model it fails saying so, which is the honest outcome on a host that
# does not serve one.
check_sh "live: 'what do you see on the kitchen camera?' through a served VLM (needs VISION_MODEL)" '
[ -n "${VISION_MODEL:-}" ] || {
    echo "VISION_MODEL is not set: name a multimodal model the server at LLM_URL serves"
    echo "(docs/research/vision-and-cameras.md §3.1 has the llama-swap entry; jarvis-core/docs/vision.md the gateway one)"
    exit 1
}
LIVE_TARGET=harness LIVE_NO_BROWSER=1 LIVE_CAPABILITY=vision \
    bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'
verify_end
