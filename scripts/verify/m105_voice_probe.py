"""M105's live half: the rig's synthetic voice, enrolled as Rig, is accepted as Rig only.

Runs against the house named by `.env` (JARVIS_URL / JARVIS_TOKEN). Enrols four
phrases in the rig's Piper voice under the label "Rig", verifies one more
utterance against everyone, asserts it is Rig and nobody else, prints every
person's score, and forgets Rig again so the house is as it was. The
operator's own numbers are printed, never asserted on — their data.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402

from testing.live.voice import Mouth  # noqa: E402

PHRASES = [
    "My voice is my passport, verify me.",
    "The quick brown fox jumps over the lazy dog.",
    "Jarvis, remember who is speaking.",
    "Rain in the afternoon, sun by five.",
]


def env(name: str) -> str:
    for path in (".env", "jarvis-core/.env"):
        try:
            for line in open(path):
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"')
        except FileNotFoundError:
            pass
    return os.environ.get(name, "")


async def main() -> int:
    base = (env("JARVIS_URL") or "http://127.0.0.1:8080").rstrip("/")
    headers = {"Authorization": f"Bearer {env('JARVIS_TOKEN')}"}
    raw = {**headers, "Content-Type": "application/octet-stream"}
    mouth = Mouth()
    async with httpx.AsyncClient(timeout=60) as http:
        status = (await http.get(f"{base}/api/voice/speaker", headers=headers)).json()
        others = [str(p.get("label")) for p in status.get("people") or [] if p.get("label") != "Rig"]
        phrases = [str(p) for p in status.get("phrases") or []][:4] or PHRASES
        try:
            for phrase in phrases:
                said = mouth.say(phrase)
                r = await http.post(
                    f"{base}/api/voice/speaker/enrol", headers=raw,
                    params={"label": "Rig", "rate": said.rate, "width": said.width}, content=said.pcm,
                )
                assert r.status_code < 300, r.text[:200]
            said = mouth.say("Remember that I take my tea with honey.")
            body = (await http.post(
                f"{base}/api/voice/speaker/verify", headers=raw,
                params={"rate": said.rate, "width": said.width}, content=said.pcm,
            )).json()
            verdict = body.get("verdict") or {}
            print("as anyone:", json.dumps({k: verdict.get(k) for k in ("accepted", "label", "nearest", "score", "threshold", "reason", "blocks")}))
            assert verdict.get("accepted") and verdict.get("label") == "Rig", (
                f"the synthetic voice was accepted as {verdict.get('label')!r} (nearest {verdict.get('nearest')!r}), expected Rig"
            )
            for who in others:
                s = (await http.post(
                    f"{base}/api/voice/speaker/verify", headers=raw,
                    params={"label": who, "rate": said.rate, "width": said.width}, content=said.pcm,
                )).json().get("verdict") or {}
                print(f"against {who}: accepted={s.get('accepted')} score={s.get('score')} threshold={s.get('threshold')} reason={s.get('reason')} blocks={s.get('blocks')}")
                assert not s.get("accepted"), f"{who}: a synthetic voice was accepted as them"
            for who in others:
                p = (await http.get(f"{base}/api/voice/speaker", headers=headers, params={"label": who})).json()
                print(f"{who}: samples={p.get('samples')} self_scores={[round(x, 2) for x in (p.get('self_scores') or [])]} suggested={p.get('suggested_threshold')} threshold={p.get('configured_threshold') or p.get('threshold')}")
                print(f"{who}: block_spreads={p.get('block_spreads')} block_limits={p.get('block_limits')}")
        finally:
            await http.delete(f"{base}/api/voice/speaker", headers=headers, params={"label": "Rig"})
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
