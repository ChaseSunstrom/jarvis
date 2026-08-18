#!/usr/bin/env python3
"""Is Jarvis able to use this model server, and if not, which step fails?

Written for the question "I pointed it at my llama-swap / LiteLLM / vLLM and it
says it cannot reach the language model". That sentence is the agent's fallback
for *every* failure — a wrong wire, a refused connection, a 401, a model name
the server does not know, a template that cannot do tool calls — so it tells
you nothing on its own. This runs the same four steps `jarvis-core` runs, in
the same order, with the same client code, and names the one that broke.

    python3 scripts/check-model-server.py http://127.0.0.1:8080/v1
    python3 scripts/check-model-server.py http://host:8080/v1 --model qwen3-8b
    python3 scripts/check-model-server.py http://litellm:4000/v1 --api-key sk-...

With no url it reads `llm:` out of jarvis-core/config/configuration.yaml, which
is the more useful check: it tests what your install is actually configured
with rather than what you meant to configure.

Exit status is 0 only if a turn with tools attached completed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "jarvis-core"))

try:
    from jarvis.integrations.llm import _detect_backend, _scalar
    from jarvis.llm.ollama import OllamaClient, OllamaError
    from jarvis.llm.openai_compat import OpenAICompatClient, normalise_base_url
except ImportError as err:  # pragma: no cover - a checkout without deps
    sys.exit(f"cannot import jarvis-core ({err}). pip install -r jarvis-core/requirements.txt")

OK, BAD, WARN = "  ok  ", " FAIL ", " warn "


def say(mark: str, text: str) -> None:
    print(f"[{mark}] {text}")


def hint(text: str) -> None:
    for line in text.strip().splitlines():
        print(f"         {line}")


# --- what the install is actually configured with --------------------------
def from_config() -> dict:
    """The `llm:` block, with `!env_var` resolved the way jarvis-core does."""
    import yaml

    path = ROOT / "jarvis-core" / "config" / "configuration.yaml"
    if not path.is_file():
        return {}

    class Loader(yaml.SafeLoader):
        pass

    def env_var(loader, node):
        parts = str(loader.construct_scalar(node)).split(None, 1)
        name = parts[0]
        default = parts[1].strip() if len(parts) > 1 else ""
        return os.environ.get(name, default)

    for tag in ("!secret", "!include", "!include_dir_named", "!include_dir_list",
                "!include_dir_merge_named", "!include_dir_merge_list"):
        Loader.add_constructor(tag, lambda ldr, node: None)
    Loader.add_constructor("!env_var", env_var)

    try:
        doc = yaml.load(path.read_text(encoding="utf-8"), Loader=Loader) or {}
    except Exception as err:
        say(WARN, f"could not parse configuration.yaml ({err}); using defaults")
        return {}
    block = doc.get("llm")
    return block if isinstance(block, dict) else {}


async def _speaks_openai(url: str, api_key: str, timeout: float) -> bool:
    """Does `<url>/v1/models` answer? Cheap, and never raises."""
    probe = OpenAICompatClient(url=url, timeout=min(timeout, 10.0), api_key=api_key or None)
    try:
        await probe.list_models()
        return True
    except Exception:
        return False
    finally:
        await probe.aclose()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", help="model server base url; default: from configuration.yaml")
    ap.add_argument("--model")
    ap.add_argument("--api-key")
    ap.add_argument("--backend", choices=("ollama", "openai"))
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    cfg = {} if args.url else from_config()
    if cfg:
        say(OK, "read the `llm:` block from jarvis-core/config/configuration.yaml")

    url = args.url or str(cfg.get("url") or cfg.get("host") or "http://127.0.0.1:11434")
    model = args.model or str(cfg.get("model") or "qwen3:8b")
    api_key = args.api_key or _scalar(cfg.get("api_key"))
    backend = args.backend or _scalar(cfg.get("backend")) or _detect_backend(url)

    print()
    say(OK, f"url      {url}")
    say(OK, f"model    {model}")
    say(OK, f"backend  {backend}" + ("" if args.backend or cfg.get("backend") else "   (inferred from the url)"))
    say(OK, f"api key  {'set' if api_key else 'not set'}")
    print()

    # --- 1. the wire ------------------------------------------------------
    # The single most common misconfiguration, and it is silent: llama-swap,
    # LiteLLM and vLLM all speak /v1/chat/completions and none of them serve
    # Ollama's /api/chat. A url written without /v1 is read as Ollama, so
    # Jarvis POSTs to a path the server has never heard of and reports the 404
    # as "could not reach the language model".
    if backend == "openai":
        client = OpenAICompatClient(url=url, model=model, timeout=args.timeout,
                                    api_key=api_key or None)
        say(OK, f"talking to {normalise_base_url(url)} (OpenAI wire)")
    else:
        client = OllamaClient(url=url, model=model, timeout=args.timeout)
        say(OK, f"talking to {url} (Ollama native wire)")

    failures = 0
    try:
        # --- 2. reachable, and what does it serve -------------------------
        served: list[str] = []
        try:
            served = await client.list_models()
            say(OK, f"model list: {len(served)} served" + (f" — {', '.join(served[:8])}" if served else ""))
        except OllamaError as err:
            say(BAD, f"cannot list models: {err}")
            text = str(err)
            if "Connect" in text or "connect" in text or "refused" in text:
                hint(
                    "Nothing is listening there from where jarvis-core is running.\n"
                    "If jarvis-core is in Docker, 127.0.0.1 is the CONTAINER's loopback:\n"
                    "use the host's LAN address, or the service name if both are in\n"
                    "the same compose project. (jarvis-core ships with network_mode:\n"
                    "host, in which case 127.0.0.1 is correct — check it is running.)"
                )
            elif "401" in text or "403" in text:
                hint("The server wants a key. Set `api_key:` in the `llm:` block.")
            elif "404" in text and backend == "ollama":
                # Not a guess. If /v1/models answers on the same host, this IS
                # an OpenAI-compatible server and the only thing wrong is that
                # a url without /v1 is read as Ollama — which is the single
                # most common way a llama-swap or LiteLLM install is pointed at
                # Jarvis, and produces a 404 that reads like the server being
                # down.
                if await _speaks_openai(url, api_key, args.timeout):
                    say(BAD, "this is an OpenAI-compatible server on the WRONG WIRE")
                    hint(
                        f"{url.rstrip('/')}/v1/models answered, but the url has no /v1\n"
                        "in it, so Jarvis read it as Ollama and posted to\n"
                        f"{url.rstrip('/')}/api/chat — which llama-swap does not serve.\n"
                        "\nFix it with EITHER of these in the `llm:` block of\n"
                        "jarvis-core/config/configuration.yaml:\n"
                        f"    url: {url.rstrip('/')}/v1\n"
                        "  or\n"
                        "    backend: openai"
                    )
                else:
                    hint(f"Wrong path. Try url: {url.rstrip('/')}/v1 with backend: openai.")
            return 1

        # --- 3. is the configured model one of them -----------------------
        # llama-swap keys its `models:` map by a name you choose; the name of
        # the GGUF file is usually NOT it. Asking for a name it does not know
        # is a per-request error, so the model list can be fine while every
        # turn fails.
        if served and model not in served:
            say(BAD, f"the server does not serve {model!r}")
            hint("It serves: " + ", ".join(served) + "\nSet `model:` to one of those.")
            failures += 1
        elif served:
            say(OK, f"{model!r} is one of them")

        # --- 4. a real turn, without tools --------------------------------
        try:
            stream = client.chat(messages=[{"role": "user", "content": "Say the word ready."}],
                                 stream=True)
            text = "".join([d async for d in stream])
            say(OK, f"plain turn streamed {len(text)} chars: {text.strip()[:60]!r}")
        except OllamaError as err:
            say(BAD, f"a plain turn failed: {err}")
            hint(
                "The server is reachable but will not complete a chat. On llama-swap\n"
                "this usually means the upstream llama.cpp failed to load the model —\n"
                "check llama-swap's own log for the swap it attempted."
            )
            return 1

        # --- 5. the same turn WITH tools ----------------------------------
        # The step that actually breaks on llama.cpp-backed servers. Jarvis
        # attaches its whole toolbox to every turn, and a model whose chat
        # template has no tool-call support makes the server 500 (or return
        # nothing) on a request that is fine without `tools`. A conversation
        # that works in llama-swap's own web UI and fails in Jarvis is almost
        # always this.
        tools = [{
            "type": "function",
            "function": {
                "name": "get_state",
                "description": "Read the state of one thing in the house.",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        }]
        try:
            stream = client.chat(
                messages=[{"role": "user", "content": "Is the back door shut?"}],
                tools=tools,
                stream=True,
            )
            text = "".join([d async for d in stream])
            calls = stream.result.tool_calls
            if calls:
                say(OK, f"tool calling works — asked for {calls[0].name}({calls[0].arguments})")
            else:
                say(WARN, "the turn completed but the model called no tool")
                hint(
                    "Not fatal: the request was accepted. But Jarvis controls the\n"
                    "house through tools, so a model that never calls one can talk\n"
                    "and cannot do anything. Check the model has a tool-capable chat\n"
                    "template (in llama-swap, the --jinja flag on the llama.cpp cmd)."
                )
        except OllamaError as err:
            say(BAD, f"a turn WITH tools attached failed: {err}")
            hint(
                "This is the interesting one: the same request succeeded without\n"
                "`tools`. The server or the model's chat template cannot do tool\n"
                "calls. For llama-swap, add --jinja to that model's cmd and use a\n"
                "GGUF whose template supports tools; every Jarvis turn sends them."
            )
            failures += 1
    finally:
        await client.aclose()

    print()
    if failures:
        say(BAD, f"{failures} problem(s) above")
        return 1
    say(OK, "this server will work with Jarvis")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
