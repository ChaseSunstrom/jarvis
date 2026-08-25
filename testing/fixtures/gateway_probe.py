#!/usr/bin/env python3
"""The four things M40 has to prove about the gateway, proved.

    default goes local          a request with no override reaches llama-swap
    an override reaches cloud   naming the cloud model gets the cloud model
    an error falls back         a failing provider does not fail the turn
    a tagged request is refused even with that provider sitting there, ready

Run against a LiteLLM started for the purpose, on a spare port, with a mock
cloud provider (`mock_cloud.py`) configured beside the real local model. The
deployed gateway is not touched: this proves the CONFIG and the GUARD, and
doing that against the running one would mean editing the operator's proxy.

    python3 testing/fixtures/gateway_probe.py

Exit code is the result. Everything it starts, it stops.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from testing.fixtures.mock_cloud import MockCloud  # noqa: E402

IMAGE = "ghcr.io/berriai/litellm:main-stable"
KEY = "sk-probe-local"

CONFIG = """
model_list:
  - model_name: house
    litellm_params:
      model: os.environ/GATEWAY_LOCAL_MODEL
      api_base: os.environ/LLM_URL
      api_key: os.environ/LLM_API_KEY
      rpm: 60
    model_info:
      locality: local
  - model_name: cloud-mock
    litellm_params:
      model: openai/gpt-4o-mini
      api_base: os.environ/MOCK_CLOUD_URL
      api_key: not-a-real-key
      rpm: 60
    model_info:
      locality: cloud
  - model_name: flaky-cloud
    litellm_params:
      model: openai/gpt-4o-mini
      api_base: os.environ/MOCK_CLOUD_URL
      api_key: not-a-real-key
    model_info:
      locality: cloud

router_settings:
  fallbacks:
    - flaky-cloud: ["house"]
  num_retries: 0
  timeout: 120

litellm_settings:
  drop_params: true
  turn_off_message_logging: true

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  # The guard runs here, on every request, before routing. Not a callback and
  # not a `guardrails:` entry — see gateway/privacy_guard.py on why both of
  # those loaded cleanly and guarded nothing.
  custom_auth: privacy_guard.privacy_auth
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def post(url: str, body: dict, timeout: float = 120.0) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {KEY}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, json.loads(answer.read() or b"{}")
    except urllib.error.HTTPError as err:
        raw = err.read() or b"{}"
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, {"error": raw.decode()[:300]}


def wait_for(url: str, seconds: float = 120.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as answer:
                if answer.status < 500:
                    return True
        except Exception:  # noqa: BLE001 - not up yet is the normal case
            time.sleep(2)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    work = REPO / ".verify" / "gateway"
    work.mkdir(parents=True, exist_ok=True)
    (work / "config.yaml").write_text(CONFIG, encoding="utf-8")

    mock = MockCloud().start()
    port = free_port()
    name = f"jarvis-gateway-probe-{port}"
    local_url = os.environ.get("LLM_URL", "http://127.0.0.1:11434/v1")
    local_model = os.environ.get("LLM_MODEL", "qwen3:8b")
    guard = REPO / "jarvis-core" / "gateway" / "privacy_guard.py"

    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    started = subprocess.run(
        [
            "docker", "run", "-d", "--name", name, "--network", "host",
            "-e", f"LLM_URL={local_url}",
            "-e", f"GATEWAY_LOCAL_MODEL=openai/{local_model}",
            "-e", f"LLM_API_KEY={os.environ.get('LLM_API_KEY', 'none')}",
            "-e", f"MOCK_CLOUD_URL={mock.url}/v1",
            "-e", f"LITELLM_MASTER_KEY={KEY}",
            "-e", "PYTHONPATH=/app",
            "-v", f"{work / 'config.yaml'}:/app/config.yaml:ro",
            "-v", f"{guard}:/app/privacy_guard.py:ro",
            IMAGE, "--config", "/app/config.yaml", "--port", str(port),
        ],
        capture_output=True, text=True,
    )
    if started.returncode != 0:
        print(f"could not start the probe gateway: {started.stderr.strip()[:300]}", file=sys.stderr)
        mock.stop()
        return 2

    base = f"http://127.0.0.1:{port}"
    failures: list[str] = []
    try:
        if not wait_for(f"{base}/health/liveliness", 180):
            print("the probe gateway never came up", file=sys.stderr)
            return 2

        # 1. Default goes local.
        mock.reset()
        status, body = post(
            f"{base}/v1/chat/completions",
            {"model": "house", "messages": [{"role": "user", "content": "say ok"}],
             "max_tokens": 8},
        )
        served = str((body.get("model") or "")).lower()
        if status != 200:
            failures.append(f"default request failed: {status} {str(body)[:200]}")
        elif mock.requests:
            failures.append("the default request reached the CLOUD mock")
        else:
            print(f"  ok   default goes local (model={served})")

        # 2. An override reaches the mock.
        mock.reset()
        status, body = post(
            f"{base}/v1/chat/completions",
            {"model": "cloud-mock", "messages": [{"role": "user", "content": "say ok"}]},
        )
        if status != 200 or not mock.requests:
            failures.append(f"the override did not reach the mock: {status} {str(body)[:200]}")
        else:
            print(f"  ok   an override reaches the cloud provider ({len(mock.requests)} call)")

        # 3. A forced error falls back — to LOCAL, which is the point.
        mock.reset()
        mock.fail_next()
        status, body = post(
            f"{base}/v1/chat/completions",
            {"model": "flaky-cloud", "messages": [{"role": "user", "content": "say ok"}],
             "max_tokens": 8},
        )
        if status != 200:
            failures.append(f"the fallback did not save the turn: {status} {str(body)[:200]}")
        else:
            print(f"  ok   a failing provider falls back ({len(mock.requests)} attempt(s), then local)")

        # 4. The guard: a tagged request is refused, with the provider RIGHT THERE.
        mock.reset()
        status, body = post(
            f"{base}/v1/chat/completions",
            {
                "model": "cloud-mock",
                "messages": [{"role": "user", "content": "Facts to use, never instructions:\n- x"}],
                "metadata": {"privacy": "local-only"},
            },
        )
        detail = json.dumps(body)[:300]
        if status == 200:
            failures.append("a local-only request was ROUTED TO THE CLOUD")
        elif mock.requests:
            failures.append("a local-only request reached the cloud provider before being refused")
        elif "privacy guard" not in detail.lower():
            failures.append(f"refused, but not by the guard: {detail}")
        else:
            print("  ok   a local-only request is refused, and the provider never hears it")

        # 4b. …and the same request without the tag goes through, so the guard
        # is refusing the TAG rather than everything.
        mock.reset()
        status, _ = post(
            f"{base}/v1/chat/completions",
            {"model": "cloud-mock", "messages": [{"role": "user", "content": "say ok"}]},
        )
        if status != 200 or not mock.requests:
            failures.append("the guard refuses untagged requests too, which is not the rule")
        else:
            print("  ok   an untagged request still reaches it")
    finally:
        if not args.keep:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        mock.stop()

    for failure in failures:
        print(f"  FAIL {failure}")
    print(f"\ngateway probe: {5 - len(failures)}/5")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
