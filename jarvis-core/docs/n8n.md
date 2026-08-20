# n8n

Point Jarvis at an n8n instance and it can read your workflows, write new
ones, and switch a misbehaving one off.

```yaml
n8n:
  url: http://127.0.0.1:5678
  api_key: !env_var N8N_API_KEY ""
```

The key comes from n8n: **Settings → n8n API → Create an API key**. Put the
value in `.env` next to `docker-compose.yml` as `N8N_API_KEY=…`; the compose
file already passes it in.

Then ask for something that needs the outside world:

> "Write me a workflow that files receipts from my email into Notion."

## Why this and not Jarvis's own automations

Jarvis already has an automation engine — triggers, conditions, actions, a
scheduler and a console for all of it. n8n is not a second one, and it should
not become one: two places a rule could live is two places to look when
something did not fire.

What n8n has that Jarvis does not, and never should, is a few hundred
maintained SaaS connectors. So:

**n8n owns the outside world. Jarvis owns the house and the conversation.**

Asked to file an expense, Jarvis writes an n8n workflow. Asked to turn the
lights off, it does not.

## The three rules

### 1. Jarvis never touches a credential

Not to read one, not to create one, not to attach one.

A node the model writes arrives carrying something like:

```json
"credentials": { "gmailOAuth2": { "id": "5", "name": "Gmail account" } }
```

…because every n8n example on the internet has one, and the model has read
them all. **That id is a guess.** Attaching it means one of three things: it
points at nothing (a confusing failure), at the wrong account (a quiet one),
or at an account this request had no business reaching.

So the block is stripped, always, and what it asked for is reported instead:

> Created 'File the receipt'. It is switched OFF. Before it can run, connect
> gmailOAuth2 for 'Gmail' in n8n (Credentials → New), attach it to the node,
> then activate the workflow.

That is the whole of "ask for connections". A human attaches credentials in
n8n, where the secrets already live and the model is not.

### 2. What Jarvis writes arrives switched off

The create payload is **built** from four keys — `name`, `nodes`,
`connections`, `settings` — rather than forwarded, so a model that sets
`active: true` is not setting anything. A workflow nobody has read has not run.

`allow_activate: false` is the default, so Jarvis cannot switch one on either.
The natural order is: Jarvis writes it, you open n8n, connect what it asked
for, and activate it there. Set `allow_activate: true` if you would rather
approve activation from the console; it stays Tier 3.

The console's own activate button is unaffected by that flag. A person
pressing it is the human the flag exists to insist on.

### 3. The model reads structure, not parameters

`read_n8n_workflow` returns node names, types, which nodes carry a credential,
and the edges. It does **not** return `parameters`, because that is where
people type an API key into an HTTP header field, a token into a bearer field,
a password into a database DSN. A read of a workflow must not be a read of
somebody's secret.

## What Jarvis will refuse to send

`workflows.py` rebuilds every workflow before it goes anywhere, and refuses
three things that would otherwise **save happily and misbehave later**:

* **two nodes with one name** — n8n wires the graph by name, so the second one
  silently inherits the first one's connections;
* **an edge to a node that is not there** — saves, draws nothing, and that
  branch simply never runs;
* **more than 120 nodes**, or more than 400 kB — nobody is going to review
  that, and it is the shape a runaway generation takes.

Missing positions and type versions are filled in rather than refused, a
workflow with no trigger is a note rather than an error (that is what a
sub-workflow looks like), and a community node type is named so you know it
has to be installed first.

## The tiers

| tool | tier | what |
|---|---|---|
| `list_n8n_workflows` | 1 | names, ids, active or not |
| `read_n8n_workflow` | 1 | one workflow's structure |
| `list_n8n_node_types` | 1 | which nodes this instance actually has |
| `check_n8n_workflow` | 1 | a dry run: what is wrong with this JSON |
| `check_n8n_health` | 1 | is a workflow connected, on, and running |
| `create_n8n_workflow` | **3** | writes it, switched off |
| `build_n8n_workflow_with_ai` | **3** | hands it to n8n's own builder |
| `deactivate_n8n_workflow` | **3** | switches one off |
| `activate_n8n_workflow` | **3** | only with `allow_activate: true` |

Creating is Tier 3 not because it is destructive — it is not — but because a
workflow is a program that will run against somebody's email and somebody's
money as soon as it is switched on, and the person who owns that should see it
being written.

A tier on the tool is not the whole gate, though: the same verbs exist as
services (`n8n.create`, `n8n.set_active`), and an **automation** calling a
service never goes through the tool layer. Both are in `GATED_SERVICES`, so an
automation asking for either is held for a human exactly as the tool is.
`n8n.list`, `n8n.get`, `n8n.executions` and `n8n.check` are not gated — holding
an automation every time it asked which workflows exist would be a
confirmation nobody can act on.

`build_n8n_workflow_with_ai` has **no service form at all**. It can stop to ask
the household a question, and an automation firing at three in the morning
that puts questions on a lock screen is not a feature. What it produces still
goes through `create_n8n_workflow`'s write path, so the write is gated twice.

## Grounding: what this n8n actually has

A model writing n8n JSON is writing against a catalogue it has never seen. It
knows from training that there is a Slack node, so it writes
`n8n-nodes-base.slack` at version 2 — and this box has 2.2, or has it under a
different package, or does not have it at all because nobody installed the
community node. n8n saves the workflow, draws a red box, and the failure turns
up days later as "the thing you set up does nothing".

So `list_n8n_node_types` reads the vocabulary of *this* instance from two
places:

- **Harvested** from the workflows already on it, through the API key Jarvis
  already has. This is the better signal of the two — it is what this box
  actually runs, at the versions it runs them.
- **Catalogued** from `GET /rest/types/nodes.json`, which needs the optional
  login below and is the full list n8n's own editor loads.

`check_n8n_workflow` is a free dry run against that: node types this instance
does not have, versions newer than it has, a missing trigger, and which
credentials somebody will have to attach. It returns findings rather than a
verdict, so a model can fix its own JSON in the next round instead of spending
an approval to discover a typo.

## Closing the loop: did it actually work?

`check_n8n_health` joins three things that already existed separately and had
never been asked together: whether the credentials are attached, whether the
workflow is switched on, and whether it has run. The interesting answer is the
third state — *connected, on, and it has never run* — which is what a schedule
in the wrong timezone looks like and is invisible from anywhere else.

It reads run status and timing only. It never passes `includeData`, because
that returns the body of every email and invoice that went through the
workflow.

## n8n's own AI builder

    n8n:
      login:
        email: !env_var N8N_LOGIN_EMAIL ""
        password: !env_var N8N_LOGIN_PASSWORD ""

Optional, off by default, and it buys three things: the instance settings, the
full node catalogue, and n8n's own AI workflow builder — all of which live on
`/rest`, which a session cookie opens and an API key cannot.

**Say what that costs.** An n8n password is strictly more powerful than an n8n
API key, and the asymmetry runs one way: a session also authenticates
`/api/v1`, while a key never authenticates `/rest`, and `/rest` includes the
endpoint that mints API keys. Use a **dedicated non-owner n8n user**, and put
the password in the environment rather than in the file.

### Whether the builder is even available to you

Probably not, and it is worth knowing why before you spend an evening on it.
n8n's AI builder is gated by a signed licence certificate, checked by a
middleware that runs before the route. Two settings sound like they turn it on
and only one of them is yours:

| | what it means | who decides |
|---|---|---|
| `aiBuilder.setup` | is a model wired up | you, with an env var |
| `aiBuilder.enabled` | is the feature licensed | the certificate |

If you have pointed n8n's AI settings at your own local model, you have set the
first one. That does not set the second. **CHECK says which**, in one sentence,
rather than "the AI builder failed":

```bash
python3 scripts/check-n8n.py
python3 scripts/check-n8n.py --builder    # opens a real conversation, once
```

When it is not available Jarvis writes the workflow itself, which works on
every n8n and is what `list_n8n_node_types` and `check_n8n_workflow` exist to
make good. The model is told so in a sentence it can act on in the same turn,
rather than the tool quietly doing something else and reporting work it did
not do.

### How the relay works when it is available

The builder can **interrupt** — stop to ask a question, propose a plan, or ask
permission to fetch a URL — and wait for an answer. A tool cannot answer that:
a Tier-3 tool raises an approval, returns, and the turn ends. So
`build_n8n_workflow_with_ai` starts a **background task** and returns
immediately, and that task owns the conversation.

When the builder asks something, it arrives as an ordinary approval card — on
the console and on the phone — and the task goes to `blocked`, which the
progress UI draws as "waiting for you" rather than a spinner. Every relayed
question is marked as coming from an outside source, unconditionally: the words
were composed by a different AI and are about to be rendered on somebody's lock
screen.

Three things the relay does not get to bend:

- the workflow goes through `create_n8n_workflow`'s path, so a builder that
  sets `active: true` or guesses a credential id is not setting anything
- an unanswered permission-to-fetch resolves to **deny**, never to a domain
  and never to everything
- the transcript stays on the task for the console; the model gets one
  sentence and the list of credentials to connect

## What is deliberately missing

**Deleting.** Jarvis does not delete a workflow, the same way it does not
delete a repository. Deactivate it and remove it yourself.

**Running.** n8n's public API has no "run this workflow" endpoint — running one
means calling its webhook, which is an ordinary HTTP call. Add it on the Tools
page with whatever tier that particular workflow deserves.

**Creating credentials.** See rule 1.

## If it does not work

Press **CHECK** on the console's n8n panel, or:

```bash
curl -H "Authorization: Bearer $JARVIS_TOKEN" http://127.0.0.1:8080/api/n8n/check
```

It makes the smallest real API call there is and reports exactly what came
back. That matters more than usual here: this client was written against n8n's
documentation rather than against a live instance, and n8n's public API has
moved between versions. A wrong guess shows up as one sentence naming the
status and the path — not as an empty workflow list you cannot explain.

A 401 means the key; a 404 on `/api/v1/workflows` usually means an n8n too old
for the public API; HTML back instead of JSON means the URL is the editor or a
reverse proxy rather than the API.
