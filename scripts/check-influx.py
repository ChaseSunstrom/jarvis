#!/usr/bin/env python3
"""Is the operator's InfluxDB reachable, and which one is it?

The adapter is proven offline against a fake of each generation
(`jarvis-core/tests/test_metrics_influx.py`). This is the other half: it talks
to the database the operator actually has, which no test on a build machine can
do. That is why the claim in `docs/verification.md` is Scripted rather than
Automated.

    python3 scripts/check-influx.py                    # uses INFLUX_* from the environment
    python3 scripts/check-influx.py http://nas:8086 --bucket homelab

Exit codes: 0 reachable and queryable · 1 unreachable or misconfigured.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jarvis-core"))

from jarvis.metrics import Window  # noqa: E402
from jarvis.metrics.sources.influx import InfluxSource  # noqa: E402


class Standalone:
    """Enough of a Jarvis for a source that only needs somewhere to hang."""

    data: dict = {}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default=os.environ.get("INFLUX_URL", ""))
    parser.add_argument("--token", default=os.environ.get("INFLUX_TOKEN", ""))
    parser.add_argument("--org", default=os.environ.get("INFLUX_ORG", ""))
    parser.add_argument("--bucket", default=os.environ.get("INFLUX_BUCKET", ""))
    parser.add_argument("--series", default="", help="measurement.field to sample")
    args = parser.parse_args()

    if not args.url:
        print("No URL. Set INFLUX_URL or pass one.", file=sys.stderr)
        return 1

    source = InfluxSource(
        Standalone(),
        {"url": args.url, "token": args.token, "org": args.org, "bucket": args.bucket},
    )
    try:
        generation, version = await source.probe()
        if not generation:
            print(f"FAIL  nothing answered at {args.url}", file=sys.stderr)
            return 1
        print(f"ok    InfluxDB {version} (generation {generation}) at {args.url}")

        healthy, why = await source.healthy()
        print(f"{'ok   ' if healthy else 'FAIL '} {why}")
        if not healthy:
            return 1

        series = await source.list_series()
        print(f"ok    {len(series)} series in {args.bucket!r}")
        for info in series[:10]:
            print(f"        {info.key}")

        key = args.series or (series[0].key if series else "")
        if not key:
            print("FAIL  nothing to sample: the bucket is empty", file=sys.stderr)
            return 1
        answers = await source.query([key], Window.last(3600.0))
        answer = answers[0]
        if answer.error:
            print(f"FAIL  {key}: {answer.error}", file=sys.stderr)
            return 1
        real = [p for p in answer.points if p.value is not None]
        print(f"ok    {key}: {len(real)} point(s) in the last hour")
        if not real:
            print("      (reachable and queryable, but nothing was written in that window)")
        return 0
    finally:
        await source.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
