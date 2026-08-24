"""Does the reply *mean* the right thing?

Used only where a deterministic check cannot be: "confirms the hall light is
on" is satisfied by "Done." and by "The hall light is on, Sir." and by "Of
course — it's on now.", and a substring match on any of those would fail the
other two. Everything that CAN be checked against the house is checked against
the house instead; `PROCESS.md`'s rule about proof applies to this file
hardest, because a model marking a model's homework is the weakest evidence in
the repository.

Three guards against that weakness:

* The judge is asked to answer `{"ok": bool, "why": "<one line>"}` and its
  reason is logged with every verdict, so a suite that passes for silly reasons
  reads as silly.
* It is given the *criterion* and the *reply*, never the scenario, the tools
  used or the house state — a judge that can see the intent finds a way to
  agree with it.
* A judge that cannot be reached is an error, never a pass.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import httpx

from . import LiveError

#: The same endpoint Jarvis itself uses. There is no second model on this box,
#: and a judge on a cloud API would break the one hard constraint this whole
#: system has.
LLM_URL = (os.environ.get("LLM_URL") or os.environ.get("OLLAMA_URL") or "").rstrip("/")
LLM_MODEL = os.environ.get("LLM_JUDGE_MODEL") or os.environ.get("LLM_MODEL") or ""

PROMPT = """You are grading one reply from a voice assistant.

CRITERION: {criterion}

REPLY: {reply}

Does the reply satisfy the criterion? Be strict about facts and lenient about
wording: a different phrasing that means the same thing passes; a reply that
adds a fact the criterion did not ask for still passes unless it contradicts
it; a reply that dodges, asks a question instead, or claims something the
criterion says did not happen, fails.

Answer with JSON only: {{"ok": true, "why": "<one short line>"}}
"""


@dataclass
class Verdict:
    ok: bool
    why: str
    criterion: str = ""
    reply: str = ""


def _parse(raw: str) -> tuple[bool | None, str]:
    text = str(raw or "")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    body = fenced.group(1) if fenced else None
    if body is None:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        body = brace.group(0) if brace else ""
    if body:
        try:
            data = json.loads(body)
            if isinstance(data, dict) and "ok" in data:
                return bool(data["ok"]), str(data.get("why") or "")[:200]
        except ValueError:
            pass
    # Models answer in words about a quarter of the time, whatever the prompt.
    lowered = text.strip().lower()
    if lowered.startswith(("yes", "true", "pass")):
        return True, text.strip()[:200]
    if lowered.startswith(("no", "false", "fail")):
        return False, text.strip()[:200]
    return None, text.strip()[:200]


class Judge:
    """A local model, asked one small question at a time."""

    def __init__(self, url: str = "", model: str = "", timeout: float = 120.0) -> None:
        self.url = (url or LLM_URL).rstrip("/")
        self.model = model or LLM_MODEL
        self.timeout = timeout
        self.verdicts: list[Verdict] = []

    def available(self) -> bool:
        return bool(self.url and self.model)

    async def check(self, criterion: str, reply: str) -> Verdict:
        if not self.available():
            raise LiveError(
                "no local model to judge with: set LLM_URL and LLM_MODEL "
                "(this suite must not reach a cloud API)"
            )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": PROMPT.format(criterion=criterion, reply=reply)}
            ],
            "temperature": 0,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                answer = await http.post(f"{self.url}/chat/completions", json=payload)
                answer.raise_for_status()
                body = answer.json()
        except Exception as err:  # noqa: BLE001 - the rig must name its own failures
            raise LiveError(f"the judge at {self.url} could not be reached: {err}") from err

        content = ""
        for choice in body.get("choices") or []:
            content = str((choice.get("message") or {}).get("content") or "")
            if content:
                break
        ok, why = _parse(content)
        if ok is None:
            raise LiveError(f"the judge did not answer usefully: {content[:200]!r}")
        verdict = Verdict(ok=ok, why=why, criterion=criterion, reply=reply)
        self.verdicts.append(verdict)
        return verdict
