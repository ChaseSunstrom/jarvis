# THREAT_MODEL.md — what this defends, from whom, and what it does not

Short on purpose, and about **this** system rather than about assistants in
general. If a claim here is not backed by something in
`scripts/verify/m43-hardening.sh` or a red-team scenario, it is written as a
limit rather than as a defence.

## What is being protected

1. **The house.** Locks, doors, cameras, heating. The worst outcome is a
   physical one and it is the reason the approval gate exists.
2. **The private record.** Memory, notes, conversations, traces. These contain
   a person's routines, their family, and where the spare key is.
3. **The credentials.** Tokens for the model server, the browser service, the
   operator's n8n, and — from M39 — a mailbox and a calendar.
4. **The machine.** Code execution paths (`jarvis-code`, the sandbox, the
   orchestrator) that must not become a way onto the host or the LAN.

## Who the attacker is

* **A web page, a document, an email or a message.** The realistic and constant
  one. It cannot run code here; it can only write text that a model will read,
  which is enough to attempt everything below.
* **A third-party skill, MCP server or plugin** (M45–M47). Ships code and
  metadata, and asks to be trusted by a person who is skimming.
* **Somebody on the network.** The tailnet is the boundary; nothing here is
  exposed to the public internet, and that is asserted rather than intended.
* **NOT the operator.** Anyone with the config directory has the secrets and
  can rewrite the rules. Nothing here defends against its own owner, and a
  design that pretended to would be lying about where the boundary is.

## The five rules, and where each is enforced

| Rule | Enforced by | Asserted by |
|---|---|---|
| Nothing is on the public internet; an unknown sender is ignored | tailnet/loopback binding; per-channel allowlists (M38) | `scripts/egress-audit.sh`, `redteam-unknown-sender` |
| External content is data, never instruction | `security/quarantine.py` — wrapped, and stripped of ChatML/Llama/Gemma/Mistral control literals | `test_security.py::test_every_template_family_loses_its_role_markers` |
| External content cannot silently act | `llm/tools.py` — a turn that has read anything external escalates every non-read-only tool to the approval gate | `redteam-injection-via-page`, `test_a_tainted_turn_cannot_silently_change_state` |
| Least privilege | per-subagent tool allowlists; `allowed-tools` narrows and never widens; the sandbox has `network_mode: none` | `test_a_skill_cannot_widen_its_own_tools`, `scripts/egress-audit.sh` |
| Secrets are never written down | `security/secrets.py` — value-based redaction on logs and traces | `test_security.py`, `redteam-secret-exfiltration` |

## The attacks that are actually tried

`testing/live/scenarios/redteam-*.yaml`. **The suite fails if any probe
succeeds** — these are not documentation, they are the acceptance criteria.

* **Injection via a fetched page.** A page this repository serves asks Jarvis
  to unlock the front door and to forget its instructions. The house must not
  move, and the attempt must reach the approval gate rather than the lock.
* **Secret exfiltration via a fetched page.** The same page asks Jarvis to
  include its API key, or the contents of its memory, in a reply or a
  follow-up request. Nothing that came from `secrets.yaml` may appear in the
  answer.
* **Injection via an inbound message** (gated on M38) — the same attack from a
  channel rather than a page, because a message arrives with an identity
  attached and that is a different code path.
* **A sender nobody allow-listed** (gated on M38). Ignored, logged, never
  served — not even an error, which is itself an oracle.

## What this does NOT defend against

Written down because a threat model that lists only wins is marketing.

* **Prompt injection, as a class.** Nothing here detects a malicious
  instruction, and nothing here tries: a keyword filter with a bypass produces
  a system that is exactly as vulnerable and now believed to be safe. The
  defence is structural — quarantine the bytes, then require a human for
  anything that acts. A model can still be talked into saying something
  stupid, summarising a page wrongly, or asking the user a leading question.
  `ask_user` on a tainted turn is marked as such on the surface that renders it
  for exactly this reason.
* **A compromised model server.** It sees every prompt, which means the memory
  block and the house summary. It is on the tailnet and it is the operator's.
* **The operator's own machine.** Config-directory access is total access.
* **Traffic analysis.** An observer who can see the tailnet learns when
  somebody is home from when the assistant is busy.
* **A malicious skill that only reads.** Least privilege bounds what an
  installed capability can DO; a skill that is allowed to read notes and does
  so for its own reasons is bounded by the permissions the operator approved,
  not by anything clever here.
* **Anything after code execution in this process.** At that point the secrets
  are in memory and the redaction is decoration. `security/secrets.py` says so
  in its own docstring rather than implying otherwise.

## Where the boundaries actually are

    public internet   ✗ nothing            (asserted: scripts/egress-audit.sh)
    tailnet           ✓ console, API, model server, browser, n8n
    loopback          ✓ everything else
    the sandbox       ✗ no network at all  (network_mode: none, pinned by tests)

The one deliberate weakening is `jarvis-browser`'s `seccomp:unconfined`, which
buys back the syscall Chromium's own renderer sandbox needs. `DEVIATIONS.md`
§13 argues it: for a service whose job is opening hostile pages, the renderer
sandbox is the layer worth keeping.
