#!/usr/bin/env python3
"""Snapshot the numbers a toolbelt change is allowed to move, and compare two.

`docs/TOOLING_DECISIONS.md` says a component earns its place by moving a
number. This is the tape measure. It does not run anything — it reads the
artefacts the evals already write, so a snapshot is cheap and can be taken
either side of a change without paying for the change twice.

    python3 scripts/verify/toolbelt_baseline.py --out .verify/toolbelt/before.json
    #  ... add the service, wire it in, re-run the evals ...
    python3 scripts/verify/toolbelt_baseline.py --out .verify/toolbelt/after.json
    python3 scripts/verify/toolbelt_baseline.py --compare before.json after.json

`--compare` exits non-zero when something got worse. What "worse" means is per
metric and written down in `DIRECTION` below, because the alternative — one
tolerance for rates and latencies alike — is either a latency check that fires
on noise or a pass rate that can quietly lose a case.

A metric that is present in one snapshot and missing from the other is
reported, never ignored: the usual way a comparison flatters a change is that
the eval it would have failed did not run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
VERIFY = REPO / ".verify"

#: Where each number comes from. A source that is missing is recorded as
#: missing rather than skipped — see `Snapshot.gaps`.
SOURCES = {
    "scorecard": VERIFY / "live" / "scorecard.json",
    "live": VERIFY / "live" / "results.json",
}

#: metric -> ("up" | "down"), and how much movement is noise.
#:
#: `up` means higher is better and ANY drop is a regression: these are rates
#: over a handful of cases, so a drop is a case that stopped working, not a
#: fluctuation. `down` means lower is better and carries a band, because a
#: latency measured on a shared four-vCPU box moves by tens of percent between
#: runs and a check that fires on that is a check people learn to ignore.
DIRECTION: dict[str, tuple[str, float]] = {
    "intelligence.context_retention": ("up", 0.0),
    "intelligence.routing": ("up", 0.0),
    "intelligence.reasoning": ("up", 0.0),
    "intelligence.instructions": ("up", 0.0),
    "intelligence.graceful_failure": ("up", 0.0),
    "intelligence.routing_accuracy": ("up", 0.0),
    "speech.wer_round_trip": ("down", 0.02),
    "latency.idle_ttft": ("down", 0.25),
    "latency.idle_total": ("down", 0.25),
    "latency.under_load_ttft": ("down", 0.25),
    "latency.under_load_total": ("down", 0.25),
    "live.scenarios_passed": ("up", 0.0),
    "live.turns_passed": ("up", 0.0),
    "live.routing_accuracy": ("up", 0.0),
    "live.wer_mean": ("down", 0.02),
    "live.round_trip_median": ("down", 0.25),
}


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def collect() -> dict[str, Any]:
    """Every metric this repository can currently measure, and what is missing."""
    metrics: dict[str, float] = {}
    gaps: list[str] = []

    card = _read(SOURCES["scorecard"])
    if card is None:
        gaps.append(f"{SOURCES['scorecard'].relative_to(REPO)} (run evals/intelligence/run.py)")
    else:
        for section in ("context_retention", "routing", "reasoning", "instructions",
                        "graceful_failure"):
            rate = (card.get(section) or {}).get("rate")
            if rate is not None:
                metrics[f"intelligence.{section}"] = float(rate)
        accuracy = (card.get("routing") or {}).get("accuracy")
        if accuracy is not None:
            metrics["intelligence.routing_accuracy"] = float(accuracy)
        wer_out = (card.get("speech") or {}).get("wer_mean")
        if wer_out is not None:
            metrics["speech.wer_round_trip"] = float(wer_out)
        for condition in ("idle", "under_load"):
            row = (card.get("latency") or {}).get(condition) or {}
            for stage in ("ttft", "total"):
                if row.get(stage) is not None:
                    metrics[f"latency.{condition}_{stage}"] = float(row[stage])

    live = _read(SOURCES["live"])
    if live is None:
        gaps.append(f"{SOURCES['live'].relative_to(REPO)} (run scripts/verify/live_interaction.sh)")
    else:
        totals = live.get("totals") or {}
        scenarios = totals.get("scenarios") or 0
        turns = totals.get("turns") or 0
        if scenarios:
            metrics["live.scenarios_passed"] = round(totals.get("scenarios_passed", 0) / scenarios, 4)
        if turns:
            metrics["live.turns_passed"] = round(totals.get("turns_passed", 0) / turns, 4)
        for key, name in (("routing_accuracy", "live.routing_accuracy"),
                          ("wer_mean", "live.wer_mean"),
                          ("round_trip_median", "live.round_trip_median")):
            if totals.get(key) is not None:
                metrics[name] = float(totals[key])

    unknown = sorted(set(metrics) - set(DIRECTION))
    return {"metrics": metrics, "gaps": gaps, "unknown": unknown}


def compare(before: dict[str, Any], after: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(regressions, notes). A metric on one side only is a note, never silence."""
    old = before.get("metrics") or {}
    new = after.get("metrics") or {}
    regressions: list[str] = []
    notes: list[str] = []

    for name in sorted(set(old) | set(new)):
        if name not in old:
            notes.append(f"{name}: new in the second snapshot ({new[name]})")
            continue
        if name not in new:
            regressions.append(
                f"{name}: measured before ({old[name]}) and NOT after — "
                "an eval that did not run cannot be an improvement"
            )
            continue
        way, band = DIRECTION.get(name, ("up", 0.0))
        before_value, after_value = float(old[name]), float(new[name])
        if way == "up":
            if after_value < before_value - band:
                regressions.append(f"{name}: {before_value} -> {after_value} (lower is worse)")
        else:
            allowed = before_value * (1.0 + band) + (1.0 if band else 0.0)
            if after_value > allowed:
                regressions.append(
                    f"{name}: {before_value} -> {after_value} "
                    f"(over the {allowed:.2f} this run was allowed)"
                )
        if abs(after_value - before_value) > 1e-9 and name not in {r.split(":")[0] for r in regressions}:
            notes.append(f"{name}: {before_value} -> {after_value}")
    return regressions, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="", help="write a snapshot here")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    parser.add_argument("--allow-gaps", action="store_true",
                        help="snapshot even when an eval has not been run")
    args = parser.parse_args(argv)

    if args.compare:
        before, after = (_read(Path(p)) for p in args.compare)
        if before is None or after is None:
            print("both snapshots must exist and be readable", file=sys.stderr)
            return 2
        regressions, notes = compare(before, after)
        for note in notes:
            print(f"  moved: {note}")
        for regression in regressions:
            print(f"WORSE: {regression}")
        if not notes and not regressions:
            print("nothing moved")
        return 1 if regressions else 0

    snapshot = collect()
    for gap in snapshot["gaps"]:
        print(f"missing: {gap}")
    for name in snapshot["unknown"]:
        print(f"note: {name} has no entry in DIRECTION, so a comparison cannot judge it")
    for name, value in sorted(snapshot["metrics"].items()):
        print(f"  {name}: {value}")
    if snapshot["gaps"] and not args.allow_gaps:
        print("refusing to write a snapshot with a missing eval "
              "(pass --allow-gaps if that is really what you want)", file=sys.stderr)
        return 1
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        print(f"snapshot: {out} ({len(snapshot['metrics'])} metric(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
