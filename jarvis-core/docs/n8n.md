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
| `create_n8n_workflow` | **3** | writes it, switched off |
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
