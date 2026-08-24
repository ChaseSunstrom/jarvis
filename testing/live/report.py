"""Scoring: WER, accuracy, latency — and a table a person can read.

Nothing here decides whether a scenario passed; the runner does that. This
turns a pile of results into the numbers the brief asks for, and into
`docs/LIVE_TEST_REPORT.md`.

Every number is measured. There is no place in this file where a threshold can
be met by rounding, and no aggregate that hides a zero-sample denominator: a
rate over nothing is reported as `n/a`, never as 100 %.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_WORD = re.compile(r"[a-z0-9']+")

#: Numbers, both ways. Whisper writes "21", a scenario writes "twenty one";
#: both are the same recognition and only one of them is a spelling. WER is
#: meant to measure what was misheard, so this is normalised away before
#: counting — the standard practice for the metric, not a thumb on the scale.
_NUMBERS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
}

#: The model is `en_US`; the house is British. "favourite colour" coming back
#: as "favorite color" is the recogniser being right in its own dialect.
_SPELLINGS = {
    "favourite": "favorite", "colour": "color", "colours": "colors",
    "realise": "realize", "recognise": "recognize", "organise": "organize",
    "metre": "meter", "metres": "meters", "centre": "center",
    "programme": "program", "grey": "gray", "aluminium": "aluminum",
    "apologise": "apologize", "summarise": "summarize", "analyse": "analyze",
}


def normalise(text: str) -> list[str]:
    """The words that matter, in one dialect and one notation."""
    out: list[str] = []
    for word in _WORD.findall(str(text or "").lower()):
        word = _SPELLINGS.get(word, word)
        word = _NUMBERS.get(word, word)
        out.append(word)
    # "twenty one" -> "21": two normalised numbers side by side, the second a
    # unit, are one spoken number.
    joined: list[str] = []
    for word in out:
        if (
            joined
            and joined[-1].isdigit()
            and word.isdigit()
            and len(joined[-1]) == 2
            and joined[-1].endswith("0")
            and len(word) == 1
        ):
            joined[-1] = str(int(joined[-1]) + int(word))
            continue
        joined.append(word)
    return joined


def words(text: str) -> list[str]:
    return normalise(text)


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate: edits / reference words. 0.0 is perfect.

    Levenshtein over words, not characters: "turn on the light" heard as "turn
    on the lights" is one word wrong out of four, and character distance would
    call it 0.06 — flattering a recogniser that changed the meaning.
    """
    ref, hyp = words(reference), words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        current = [i]
        for j, h in enumerate(hyp, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h))
            )
        previous = current
    return previous[-1] / len(ref)


@dataclass
class TurnResult:
    scenario: str
    capability: str
    variant: str
    index: int
    said: str
    heard: str = ""
    reply: str = ""
    reply_heard: str = ""
    ok: bool = True
    failures: list[str] = field(default_factory=list)
    judge_reasons: list[str] = field(default_factory=list)
    latency: dict[str, float] = field(default_factory=dict)
    wer: float | None = None
    routed: str = ""
    routed_expected: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "capability": self.capability,
            "variant": self.variant,
            "turn": self.index,
            "said": self.said,
            "heard": self.heard,
            "reply": self.reply,
            "reply_heard": self.reply_heard,
            "ok": self.ok,
            "failures": self.failures,
            "judge": self.judge_reasons,
            "latency": {k: round(v, 3) for k, v in self.latency.items()},
            "wer": None if self.wer is None else round(self.wer, 4),
            "routed": self.routed,
            "routed_expected": self.routed_expected,
        }


@dataclass
class ScenarioResult:
    name: str
    capability: str
    variant: str
    gated_on: str = ""
    ok: bool = True
    error: str = ""
    turns: list[TurnResult] = field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capability": self.capability,
            "variant": self.variant,
            "gated_on": self.gated_on,
            "ok": self.ok,
            "error": self.error,
            "seconds": round(self.seconds, 2),
            "turns": [t.as_dict() for t in self.turns],
        }


def _rate(good: int, total: int) -> float | None:
    return None if total <= 0 else good / total


def summarise(results: list[ScenarioResult]) -> dict[str, Any]:
    turns = [turn for result in results for turn in result.turns]
    wers = [t.wer for t in turns if t.wer is not None]
    routed = [t for t in turns if t.routed_expected]
    totals: dict[str, Any] = {
        "scenarios": len(results),
        "scenarios_passed": sum(1 for r in results if r.ok),
        "turns": len(turns),
        "turns_passed": sum(1 for t in turns if t.ok),
        "wer_mean": round(statistics.fmean(wers), 4) if wers else None,
        "wer_max": round(max(wers), 4) if wers else None,
        "wer_samples": len(wers),
        "intent_accuracy": _rate(sum(1 for t in turns if t.ok), len(turns)),
        "routing_accuracy": _rate(
            sum(1 for t in routed if t.routed == t.routed_expected), len(routed)
        ),
        "routing_samples": len(routed),
    }
    round_trips = [t.latency.get("total") for t in turns if t.latency.get("total")]
    totals["round_trip_median"] = (
        round(statistics.median(round_trips), 3) if round_trips else None
    )
    totals["round_trip_p95"] = (
        round(sorted(round_trips)[int(len(round_trips) * 0.95) - 1], 3)
        if len(round_trips) >= 20
        else None
    )
    by_capability: dict[str, dict[str, int]] = {}
    for result in results:
        row = by_capability.setdefault(result.capability, {"passed": 0, "total": 0})
        row["total"] += 1
        row["passed"] += 1 if result.ok else 0
    totals["by_capability"] = by_capability
    return totals


def latency_table(results: list[ScenarioResult]) -> dict[str, dict[str, float | None]]:
    """Median and p95 per stage, over every turn that reported one."""
    stages = ("stt", "ttft", "intent", "tts", "total")
    collected: dict[str, list[float]] = {stage: [] for stage in stages}
    for result in results:
        for turn in result.turns:
            for stage in stages:
                value = turn.latency.get(stage)
                if isinstance(value, (int, float)) and value > 0:
                    collected[stage].append(float(value))
    out: dict[str, dict[str, float | None]] = {}
    for stage, values in collected.items():
        out[stage] = {
            "n": len(values),
            "median": round(statistics.median(values), 3) if values else None,
            "p95": round(sorted(values)[int(len(values) * 0.95) - 1], 3)
            if len(values) >= 20
            else None,
        }
    return out


def write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def markdown(results: list[ScenarioResult], totals: dict[str, Any],
             latencies: dict[str, dict[str, Any]]) -> str:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.1f} %"

    lines = ["| capability | scenarios | passed | rate |", "|---|---|---|---|"]
    for capability, row in sorted(totals["by_capability"].items()):
        lines.append(
            f"| {capability} | {row['total']} | {row['passed']} | "
            f"{pct(_rate(row['passed'], row['total']))} |"
        )
    table = "\n".join(lines)

    stage_lines = ["| stage | n | median | p95 |", "|---|---|---|---|"]
    for stage, row in latencies.items():
        median = "n/a" if row["median"] is None else f"{row['median']:.2f} s"
        p95 = "n/a" if row["p95"] is None else f"{row['p95']:.2f} s"
        stage_lines.append(f"| {stage} | {row['n']} | {median} | {p95} |")
    return f"{table}\n\n" + "\n".join(stage_lines)
