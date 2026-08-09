# Cross-device conversation — Jarvis reaching *you*

Everything else in Jarvis is request/response: you speak, it answers. This is
the other direction. Something happens — a build fails on the desktop, a
long task finishes, an automation needs a decision — and Jarvis finds you on
whichever device you're actually at, tells you, and can **ask a question and
wait for your answer**.

## The three pieces

| Piece | Where | Job |
|---|---|---|
| `jarvis/presence.py` | jarvis-core | Ranks devices by how likely you are to see/hear something *right now*. Pure logic, no I/O. |
| `jarvis/integrations/companion/` | jarvis-core | `notify` / `ask` / `handoff` services + escalation + queueing. Transport-agnostic. |
| device channel | phone / desktop / web | Receives `jarvis_message`, renders it, sends the answer back. |

## Presence

Each connected client reports cheap signals — screen on, locked, last
interaction, driving, zone, audio available, muted — and presence turns them
into a **reach** level:

```
ACTIVE      you interacted here in the last ~2 minutes   ← strongest signal
PRESENT     screen on and unlocked
IDLE        screen on, or used in the last ~10 minutes
BACKGROUND  connected but asleep
ABSENT      no signal in 15 minutes, or disconnected
```

## Routing policy

`route(need, importance, prefer_device)` returns *where* and *how*:

- **Driving beats recency** for anything audible. If your phone says you're
  driving, that's where it speaks — even if you were just typing at the desk.
- **A question needs a device you can answer on** (reach ≥ IDLE). If nothing
  qualifies, the question is queued rather than shouted into an empty room.
- **Muted devices are a last resort**, not excluded — if it's the only device
  you have, a quiet notification beats losing the message.
- **No audio? Speech downgrades to a notification** on that device rather than
  being dropped.
- **Nothing reachable → queued**, and delivered when a device comes back.
  Unless it's `critical`, which lands on your most-recently-seen device so
  it's waiting for you.

## Asking a question and getting an answer

This is the part that makes automations conversational. `companion.ask` blocks
and returns the answer, so an automation can branch on it:

```yaml
- action: companion.ask
  data:
    question: "The nightly backup found 3 GB of new photos. Upload them now?"
    options: ["yes", "later"]
    timeout: 300
  response_variable: answer
- if: "{{ answer.answer == 'yes' }}"
  then:
    - action: script.upload_photos
```

If you dismiss it on the phone, it **escalates** to the next-best device
automatically. If nobody answers before the timeout, it returns
`status: timeout` — the automation decides what that means, rather than
hanging forever.

## Conversation continuity

Messages carry a `conversation_id`. Answer on your phone and the reply lands
back in the same conversation the desktop started — so "yes" means the right
thing without re-establishing context. `companion.handoff` moves an in-flight
conversation to another device deliberately.

## Wire protocol

Server → device:

```json
{"type": "jarvis_message", "message_id": "a1b2c3", "kind": "ask",
 "mode": "ask", "text": "Deploy to production?", "options": ["yes", "no"],
 "conversation_id": "conv-7", "importance": "high", "timeout_s": 120}
```

`kind`: `say` (aloud) · `ask` (needs an answer) · `notify` (quiet).
`mode` is what presence decided: `speak` · `ask` · `notify`.

Device → server:

```json
{"type": "jarvis_message_result", "message_id": "a1b2c3",
 "status": "answered", "answer": "no"}
```

`status`: `answered` · `dismissed` · `timeout` · `undeliverable`.
Anything but `answered` triggers escalation to the next device.

## Security notes

- The companion module never touches sockets; the API layer injects a
  transport. That keeps routing testable and means a compromised transport
  can't invent presence.
- A device answering a question it was never sent is ignored (unknown
  `message_id` → no-op).
- **Only a human answers a question.** `mode` is a *presentation* hint; it
  cannot demote a `kind: ask` into something the device acknowledges on the
  user's behalf. Every device reports `answered` with an empty string to mean
  "delivered" — there is no fifth status — so a question rendered as a
  notification would resolve the waiting `companion.ask` with a reply nobody
  gave *and* stop the escalation that would have reached them. Three
  independent places refuse it: `presence.route()` never downgrades the mode of
  a question, the phone and desktop parsers force `kind: ask` back to
  `mode: ask`, and the manager treats a blank `answered` to an `ask` as a
  `dismissed`.
- Proactive messages are **information and questions only**. They cannot
  execute anything: acting on an answer still goes through the normal
  service/tool path, and Tier-3 actions still require their own consent
  prompt on the device. An answer of "yes" is data, not an authorisation
  token.
