#!/usr/bin/env python3
"""Full stt→tts round trip through `assist_pipeline/run` on a live jarvis-core.

This is the one check that actually pushes *audio* through the stack. The
broader `scripts/e2e-smoke.sh` boots a throwaway server and exercises REST,
the websocket and a conversation turn, but it only proves the Wyoming ports
are open — it deliberately skips transcription because that needs real audio.
This script supplies it: a synthetic 1s 16kHz sine tone, streamed with the
binary framing every client uses (one handler-id byte, then Int16LE PCM; a
lone id byte ends the audio), asserting stt-end, intent output and tts-end
come back. It is the same protocol the HUD and the Android app speak, so a
pass here means a real client will work.

Env: JARVIS_URL (http://127.0.0.1:8080), JARVIS_TOKEN, JARVIS_PIPELINE.
Prints measured latencies.

Requires: pip install websockets. Skips gracefully (exit 0 with SKIP) only
if JARVIS_TOKEN is unset — a real run must set it.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import struct
import sys
import time

JARVIS_URL = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080")
TOKEN = os.environ.get("JARVIS_TOKEN", "")
PIPELINE = os.environ.get("JARVIS_PIPELINE", "Jarvis")


def ws_url() -> str:
    return (
        JARVIS_URL.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
        + "/api/websocket"
    )


def sine_pcm(seconds=1.0, rate=16000, freq=220) -> bytes:
    n = int(seconds * rate)
    return b"".join(
        struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n)
    )


async def run() -> int:
    import websockets

    async with websockets.connect(ws_url(), max_size=None) as ws:
        assert json.loads(await ws.recv())["type"] == "auth_required"
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        auth = json.loads(await ws.recv())
        if auth["type"] != "auth_ok":
            print(f"FAIL: auth: {auth}")
            return 1

        mid = 1

        async def send(msg):
            nonlocal mid
            msg["id"] = mid
            mid += 1
            await ws.send(json.dumps(msg))
            return msg["id"]

        # find the Jarvis pipeline
        list_id = await send({"type": "assist_pipeline/pipeline/list"})
        pipeline_id = None
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == list_id and m.get("type") == "result":
                for p in m["result"]["pipelines"]:
                    if p["name"] == PIPELINE:
                        pipeline_id = p["id"]
                if pipeline_id is None:
                    pipeline_id = m["result"].get("preferred_pipeline")
                break

        run_id = await send({
            "type": "assist_pipeline/run",
            "start_stage": "stt", "end_stage": "tts",
            "input": {"sample_rate": 16000},
            "pipeline": pipeline_id,
        })

        handler = None
        t0 = time.monotonic()
        marks = {}
        pcm = sine_pcm()
        got = {"stt": None, "intent": None, "tts": None}

        async def pump_audio():
            # wait until run-start gives us the handler id
            while handler is None:
                await asyncio.sleep(0.01)
            chunk = 1024 * 2
            for i in range(0, len(pcm), chunk):
                await ws.send(bytes([handler]) + pcm[i:i + chunk])
                await asyncio.sleep(0.02)
            await ws.send(bytes([handler]))  # end-of-audio

        pumper = asyncio.create_task(pump_audio())
        try:
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                if m.get("type") != "event":
                    continue
                ev = m["event"]
                et = ev["type"]
                if et == "run-start":
                    handler = ev["data"]["runner_data"]["stt_binary_handler_id"]
                elif et == "stt-end":
                    got["stt"] = ev["data"]["stt_output"]["text"]
                    marks["stt"] = time.monotonic() - t0
                elif et == "intent-end":
                    got["intent"] = ev["data"]["intent_output"]["response"]["speech"]["plain"]["speech"]
                    marks["intent"] = time.monotonic() - t0
                elif et == "tts-end":
                    got["tts"] = ev["data"]["tts_output"]["url"]
                    marks["tts"] = time.monotonic() - t0
                elif et == "run-end":
                    break
                elif et == "error":
                    print(f"FAIL: pipeline error: {ev['data']}")
                    return 1
        finally:
            pumper.cancel()

        print("transcript:", got["stt"])
        print("response:  ", got["intent"])
        print("tts url:   ", got["tts"])
        print("latencies(s):", {k: round(v, 3) for k, v in marks.items()})
        ok = got["tts"] is not None and got["intent"] is not None
        print("PIPELINE SMOKE:", "PASS" if ok else "FAIL")
        return 0 if ok else 1


def main() -> int:
    if not TOKEN:
        print("SKIP: JARVIS_TOKEN unset — set it to run the real pipeline smoke test.")
        return 0
    try:
        import websockets  # noqa
    except ImportError:
        print("need: pip install websockets", file=sys.stderr)
        return 2
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
