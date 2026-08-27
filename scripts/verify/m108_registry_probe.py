"""M108's live half: both registries answer through the house, and the catalogue lists them.

Runs against the house named by `.env` (JARVIS_URL / JARVIS_TOKEN). Asks the
house to browse, asserts an entry from `anthropic-skills` (a skill) and one
from `mcp-registry` (an MCP server with an https URL) are listed with
`installed` known, searches for one skill by name, and asks for its plan —
which fetches the folder from GitHub, hashes it, and installs nothing.
Nothing is installed: that is the operator's decision, on the console.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from testing.harness.client import JarvisClient  # noqa: E402


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
    client = JarvisClient(base, env("JARVIS_TOKEN"))
    await client.connect()
    try:
        answer = await client.command("jarvis/extensions/browse")
        sources = answer.get("sources") or []
        entries = answer.get("entries") or []
        errors = answer.get("errors") or []
        print(f"sources: {sources}; entries: {len(entries)}; skipped: {answer.get('skipped')}; errors: {errors}")
        assert "anthropic-skills" in sources and "mcp-registry" in sources, sources
        skills = [e for e in entries if e.get("source") == "anthropic-skills"]
        servers = [e for e in entries if e.get("source") == "mcp-registry"]
        assert skills, "no skill from anthropic-skills was listed: " + json.dumps(errors)
        assert servers, "no server from mcp-registry was listed: " + json.dumps(errors)
        assert all(str(s.get("url", "")).startswith("https://") for s in servers)
        assert all("installed" in e for e in entries)
        print(f"skills: {[s['id'] for s in skills][:6]}… servers: {[s['id'] for s in servers][:4]}…")

        found = await client.command("jarvis/extensions/browse", query="canvas")
        ids = [e["id"] for e in found.get("entries") or []]
        assert "canvas-design" in ids, ids
        print(f"search 'canvas': {ids}")

        plan = await client.command("jarvis/extensions/plan", source="anthropic-skills", entry="canvas-design")
        proposal = plan.get("plan") or {}
        assert not plan.get("error"), plan
        assert "SKILL.md" in (proposal.get("files") or []), proposal
        assert len(proposal.get("sha256") or "") == 64, proposal
        print(f"plan canvas-design: {len(proposal['files'])} files, sha256 {proposal['sha256'][:12]}…, hooks {proposal.get('hooks')}")

        mcp_plan = await client.command("jarvis/extensions/plan", source="mcp-registry", entry=servers[0]["id"])
        mp = mcp_plan.get("plan") or {}
        assert not mcp_plan.get("error"), mcp_plan
        assert mp.get("kind") == "mcp" and str(mp.get("url", "")).startswith("https://") and mp.get("tier"), mp
        print(f"plan {servers[0]['id']}: url {mp['url']} tier {mp['tier']} — {mp.get('note')}")
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
