#!/usr/bin/env python3
"""Can Jarvis use this n8n, and if not, which of the three layers failed?

Written for a question source-reading cannot answer: **is n8n's own AI
workflow builder available on YOUR instance?** It is gated by a signed licence
certificate, and whether the free community registration grants it is decided
on n8n's servers, not in its source. The only way to know is to ask your box.

    python3 scripts/check-n8n.py
    python3 scripts/check-n8n.py --url http://127.0.0.1:5678 --key n8n_api_...
    python3 scripts/check-n8n.py --login jarvis@example.com --password ...
    python3 scripts/check-n8n.py --builder      # actually start a build

With no arguments it reads `n8n:` out of jarvis-core/config/configuration.yaml,
which is the more useful check: it tests what your install is configured with
rather than what you meant to configure.

`--builder` goes one step further and opens a real conversation with the
builder, printing every chunk type it sees. That is worth running once: it is
how the fixtures under `jarvis-core/tests/fixtures/` were made, and if your
n8n emits a chunk type Jarvis does not know about, this is where it shows up.
It costs tokens on whatever model n8n is pointed at, so it is opt-in.

Exit status is 0 if the public API works. The login and the builder are
reported either way, because Jarvis is useful without both.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "jarvis-core"))

try:
    from jarvis.integrations.n8n.builder import BuilderClient, BuilderError
    from jarvis.integrations.n8n.capabilities import N8nCapabilities
    from jarvis.integrations.n8n.client import N8nClient
    from jarvis.integrations.n8n.session import N8nSession
except ImportError as err:  # pragma: no cover - a checkout problem, not a bug
    print(f"Could not import jarvis-core: {err}", file=sys.stderr)
    print("Run this from a checkout, with jarvis-core's dependencies installed.")
    raise SystemExit(2) from None


def from_configuration() -> dict:
    """The `n8n:` block out of the shipped configuration, or {}."""
    path = ROOT / "jarvis-core" / "config" / "configuration.yaml"
    if not path.is_file():
        return {}
    try:
        from jarvis.config import load_config

        return dict(load_config(path).get("n8n") or {})
    except Exception as err:  # pragma: no cover - a bad config is the answer
        print(f"(could not read {path}: {err})")
        return {}


def line(label: str, ok: bool, detail: str) -> None:
    mark = "OK  " if ok else "--  "
    print(f"{mark}{label}: {detail}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="")
    parser.add_argument("--key", default="")
    parser.add_argument("--login", default="", help="n8n account email")
    parser.add_argument("--password", default="")
    parser.add_argument("--mfa", default="")
    parser.add_argument("--rest-path", default="")
    parser.add_argument(
        "--builder",
        action="store_true",
        help="open a real conversation with n8n's AI builder and dump what it sends",
    )
    parser.add_argument(
        "--prompt",
        default="Every morning at 8, fetch https://example.com and email me the title.",
        help="what to ask the builder for, with --builder",
    )
    args = parser.parse_args()

    configured = from_configuration()
    login_block = configured.get("login") if isinstance(configured.get("login"), dict) else {}
    url = args.url or str(configured.get("url") or "")
    key = args.key or str(configured.get("api_key") or "")
    email = args.login or str(login_block.get("email") or "")
    password = args.password or str(login_block.get("password") or "")
    mfa = args.mfa or str(login_block.get("mfa_code") or "")
    rest_path = args.rest_path or str(configured.get("rest_path") or "/rest")

    if not url:
        print("No n8n url — pass --url or set `n8n: url:` in configuration.yaml.")
        return 2

    print(f"n8n at {url}\n")
    client = N8nClient(url, key)
    session = N8nSession(url, email, password, mfa_code=mfa, rest_path=rest_path)
    caps = N8nCapabilities(client=client, session=session)
    await caps.refresh(force=True)

    line("public API", caps.api.available, caps.api.detail)
    line("login", caps.login.available, caps.login.detail)
    line("AI builder", caps.builder.available, caps.builder.detail)

    if args.builder:
        print("\n--- asking n8n's builder for a workflow ---")
        if not caps.login.available:
            print("Cannot: there is no session. See the login line above.")
        else:
            await _dump_a_build(session, caps, args.prompt)

    print()
    if caps.api.available:
        print("Jarvis can use this n8n.")
        if not caps.builder.available:
            print("It will write workflows itself, which works on every n8n.")
        return 0
    print("Jarvis cannot use this n8n yet — fix the public API line first.")
    return 1


async def _dump_a_build(session: N8nSession, caps: N8nCapabilities, prompt: str) -> None:
    """Open one real build and print every chunk type, once.

    The point is the taxonomy: which `type` values this n8n version actually
    emits, and whether the response really ends when the graph interrupts.
    Both are things the relay's design rests on and neither is documented.
    """
    builder = BuilderClient(session, capabilities=caps)
    seen: dict[str, int] = {}
    try:
        async for message in builder.build(prompt):
            kind = str(message.get("type") or "?")
            seen[kind] = seen.get(kind, 0) + 1
            shown = {k: v for k, v in message.items() if k != "codeSnippet"}
            if "codeSnippet" in message:
                shown["codeSnippet"] = f"<{len(str(message['codeSnippet']))} chars>"
            print(f"  {kind}: {json.dumps(shown)[:400]}")
    except BuilderError as err:
        print(f"  builder error: {err}")
    print(f"\n  chunk types seen: {', '.join(sorted(seen)) or '(none)'}")
    print("  If any of those are not in builder.py's KNOWN_TYPES, that is the finding.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
