# Hermes and Honcho: should Jarvis use them?

Asked directly: *"should we implement hermes/honcho for better
consistency/improvement for jarvis? (if they are completely private/local)"*

Short answers: **Hermes, yes — and it costs one dropdown selection, not an
integration. Honcho, not yet, and the reason is not privacy.**

## What is already here

`jarvis-core/jarvis/llm/memory.py` is the whole of Jarvis's memory today, and
its own docstring is honest about the scope: *"Bounded, in-memory conversation
history."* Twenty turns, fifteen minutes of silence, fifty conversations, and
nothing written to disk. It exists so "and the other one?" resolves, and it is
good at that.

What it means is that **Jarvis has no long-term memory at all**. Close the
conversation and everything it learned about you is gone. That is the gap
Honcho is designed to fill, so the question is a fair one.

## Hermes

Hermes is a *model*, not a service — Nous Research's open-weight family, served
by Ollama like any other. Nothing needs implementing:

```bash
ollama pull hermes4:14b
```

and pick it in the console under Settings → Assistant → Model, which is a
dropdown of whatever Ollama reports it has.

It is worth trying for one specific reason. Jarvis leans hard on tool calling —
eighteen built-in tools, plus every YAML tool and every script carrying a
`description:` — and the quality of a turn is mostly the model's willingness to
call the right tool with the right arguments rather than narrate that it would.
Hermes is tuned for exactly that, and the comparable tier to the shipped
`qwen3:8b` is `hermes4:14b`, which needs roughly twice the VRAM.

Privacy: identical to what you run now. It is weights on your machine.

Test it the honest way — same house, same ten commands, both models, and see
which one actually presses the button. `jarvis-orchestrator`'s routing eval is
the closest thing to a harness for that.

## Honcho

Honcho is a real memory layer: workspaces, peers, sessions, and a *reasoning
pipeline* that asynchronously derives facts and psychological representations
about a person from their messages. It is open source, and it is genuinely
self-hostable — FastAPI, PostgreSQL with pgvector, Redis, and an LLM for the
deriver, all in a Docker Compose.

**Is it private?** It can be. The deriver takes a configurable model provider,
so pointing it at Ollama's OpenAI-compatible endpoint (`/v1`) keeps every token
on your machine. Point it at a hosted API instead — which is the documented
default and the shape most people run — and every message you speak to Jarvis
goes to a third party for analysis. That is a configuration decision, not a
property of the software, and it is the single thing to get right.

**So why not yet.** Three costs, and none of them are privacy:

1. **It doubles the inference load, on the same GPU.** The deriver runs an LLM
   pass over messages as they arrive. Jarvis already spends that GPU on the
   conversation itself, and a voice assistant is judged on the gap between
   finishing a sentence and hearing a reply. Adding a background job that
   competes for the same card is a direct trade of latency for memory.

2. **It is three more services to keep alive.** Postgres, Redis, and the Honcho
   server, on a box whose current dependency list is Ollama and three Wyoming
   containers. Every one of them is another thing that can be down at 7am when
   you ask for the lights.

3. **The safety model has to be extended before it is safe here.** Derived
   facts are *model output about untrusted input*, and Jarvis's central rule is
   that nothing which reads untrusted content in a turn may then act on the
   house without a human. A memory layer that silently feeds derived
   conclusions into the system prompt is a way around that rule unless the
   fence is extended to cover it — which means memory-derived context has to be
   marked and treated exactly like `<untrusted_web_content>` already is.

**What to do instead, if you want memory now.** The cheap 80% is a `remember`
tool plus a store: the model writes a fact when you tell it one, facts are
listed in the prompt, you can see and delete them in the console. No second
model pass, no Postgres, no deriver, and it fails in ways you can read. It is
also the honest prerequisite for Honcho — if a flat list of facts you control
does not improve turns, an inferred psychological model of you is not going to
either.

Revisit Honcho when the memory store exists, the fence covers derived context,
and there is a second machine to run the deriver on.

Sources:
[Honcho](https://github.com/plastic-labs/honcho) ·
[a 2026 review of its architecture and deployment shapes](https://andrew.ooo/posts/honcho-plastic-labs-agent-memory-review/) ·
[a self-hosted fork wiring it to non-default providers](https://github.com/elkimek/honcho-self-hosted)
