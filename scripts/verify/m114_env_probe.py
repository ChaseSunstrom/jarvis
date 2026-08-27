"""M114's live half: a variable set from the console is live after a restart, and gone after a clear.

Runs against the house named by `.env` (JARVIS_URL / JARVIS_TOKEN). Uses
`AGENT_SEARCH_TOKEN` — documented, a secret, and unused unless AgentSearch is
configured — so the house's own settings are never touched. Sets it, asks
the house to restart, waits for it back, reads the row (source `override`,
value masked, `reveal` equal to what was set), clears it, restarts again,
and reads it gone. The process's own `_JARVIS_ENV_ORIGINAL_…` bookkeeping is
what makes "which value is live" a fact rather than a guess.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from testing.harness.client import JarvisClient  # noqa: E402

NAME = "AGENT_SEARCH_TOKEN"


def env(name: str) -> str:
    for path in (".env", "jarvis-core/.env"):
        try:
            for line in open(path):
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"')
        except FileNotFoundError:
            pass
    return os.environ.get(name, "")


async def connect(base: str, token: str, tries: int = 60) -> JarvisClient:
    last: Exception | None = None
    for _ in range(tries):
        client = JarvisClient(base, token)
        try:
            await client.connect()
            return client
        except Exception as err:  # noqa: BLE001 - the house is coming back
            last = err
            await asyncio.sleep(2)
    raise RuntimeError(f"the house did not come back: {last}")


async def restart(client: JarvisClient, base: str, token: str) -> JarvisClient:
    answer = await client.command("jarvis/system/restart")
    assert answer.get("status") == "ok", answer
    await client.close()
    await asyncio.sleep(3)
    return await connect(base, token)


async def main() -> int:
    base = (env("JARVIS_URL") or "http://127.0.0.1:8080").rstrip("/")
    token = env("JARVIS_TOKEN")
    mark = f"probe-{int(time.time())}"
    client = await connect(base, token)
    try:
        listing = await client.command("jarvis/environment/list")
        rows = {r["name"]: r for r in listing.get("variables") or []}
        assert NAME in rows and rows[NAME]["secret"] is True, sorted(rows)[:10]
        assert len(rows) >= 30, len(rows)
        print(f"catalogue: {len(rows)} variables; {NAME} set={rows[NAME]['set']} source={rows[NAME]['source']}")

        put = await client.command("jarvis/environment/set", name=NAME, value=mark)
        assert put.get("status") == "ok", put
        client = await restart(client, base, token)
        rows = {r["name"]: r for r in (await client.command("jarvis/environment/list")).get("variables") or []}
        row = rows[NAME]
        assert row["set"] is True and row["source"] == "override" and row["pending"] is False, row
        assert row["value"] != mark and row["live"] != mark, "a secret is masked in the listing"
        shown = await client.command("jarvis/environment/reveal", name=NAME)
        assert shown.get("value") == mark, shown
        print(f"after restart: {NAME} source={row['source']} live-masked={row['live']} reveal matches")

        gone = await client.command("jarvis/environment/clear", name=NAME)
        assert gone.get("status") == "ok", gone
        client = await restart(client, base, token)
        rows = {r["name"]: r for r in (await client.command("jarvis/environment/list")).get("variables") or []}
        row = rows[NAME]
        assert row["set"] is False and row["source"] != "override", row
        assert (await client.command("jarvis/environment/reveal", name=NAME)).get("value") != mark
        print(f"after clear + restart: {NAME} set={row['set']} source={row['source']}")
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
