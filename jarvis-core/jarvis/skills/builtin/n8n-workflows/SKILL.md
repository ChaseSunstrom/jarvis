---
name: n8n-workflows
description: Use whenever the user wants something done with a service outside this house — email, calendars, spreadsheets, Notion, Slack, invoicing, payments, any SaaS API — or asks about an existing n8n workflow. Covers writing a workflow, what happens to credentials, and what to tell the user afterwards.
license: Apache-2.0
---

# Reaching services outside the house

Jarvis automates the house. **n8n automates everything else.** If the request
touches somebody else's service, the answer is an n8n workflow, not an
apology.

## Before you write one

Call `list_n8n_workflows`. Two reasons: you may be about to duplicate one that
exists, and the names tell you how this household writes them.

If you need to change an existing one, `read_n8n_workflow` gives you its shape
— node names, types, which nodes have a credential, and the wiring. It does
**not** give you node parameters, and that is deliberate: people type API keys
into them.

## Find out what this n8n actually has, first

`list_n8n_node_types` tells you which node types exist on THIS instance and at
which version. Call it before writing anything. Node names and versions differ
between installs, and a workflow naming one that is not there saves without
complaint and then does nothing — the failure turns up days later as "the
thing you set up doesn't work".

Use the exact strings and version numbers it returns. If the node you want is
not listed, say so and suggest the community package rather than guessing a
name that looks plausible.

## Writing one

`create_n8n_workflow` takes ordinary n8n workflow JSON: `name`, `nodes`,
`connections`.

- Every node needs a unique `name`. n8n wires the graph BY NAME, so two nodes
  called the same thing silently merge into a graph nobody drew — the tool
  refuses this, but write it correctly the first time.
- Every `connections` entry must name nodes that exist.
- Give each node a `position` like `[220, 300]` so the canvas is readable.
- Start with a trigger node unless the user wants a sub-workflow.

## Check it before you ask for approval

`check_n8n_workflow` is free, instant, and needs no approval. It reports node
types this instance does not have, versions newer than it has, a missing
trigger, and which credentials somebody will have to attach. Fix what it finds
and check again.

Do this every time. Creating a workflow costs the user an approval, and
spending one to discover a typo wastes their attention on something you could
have found yourself.

## When the workflow is complicated

`build_n8n_workflow_with_ai` hands the request to n8n's own AI builder, which
knows this instance's nodes and expression language first-hand. Use it when
the workflow is genuinely involved, or when it touches a service you are not
confident wiring; write the simple ones yourself.

It runs in the background and **may come back with a question for the user**,
which they will see as an ordinary approval card. So tell them it is under way
and that it may ask them something. Do not describe a workflow — there is none
yet — and do not wait for it in the same turn.

If it answers that the builder is not available here, that is not a failure to
apologise for: n8n's builder is licensed separately and most self-hosted
instances do not have it. Write the workflow yourself instead, which is what
the two tools above are for.

## Answering "did that actually work?"

`check_n8n_health` on a workflow id. It says whether the credentials are
attached, whether it is switched on, and whether it has run and succeeded.
Use it whenever somebody asks about something you set up earlier rather than
saying you cannot tell.

The answer worth watching for is *connected, switched on, and never run* —
that usually means a schedule in the wrong timezone or a webhook nobody is
calling, and it looks identical to working from every other angle.

## Credentials — the part to get right

**Write the `credentials` block as you normally would.** It will be stripped
before the workflow is sent, and what it asked for is reported back to you.
That is intended: any credential id you write is a guess, and a guessed id
points at nothing, at the wrong account, or at an account this request had no
business touching.

So when the tool comes back, **tell the user exactly what to connect**, using
the `connections_needed` it returns. For example:

> Made "File the receipt". It is switched off. It needs a Gmail credential on
> the "Send email" node — add it in n8n under Credentials → New, attach it to
> that node, then switch the workflow on.

Do not say the workflow is running. It is not, and it cannot be until somebody
attaches those credentials.

## What you cannot do

- You cannot switch a workflow ON unless the operator allowed it; say the user
  should activate it in n8n.
- You cannot delete a workflow. `deactivate_n8n_workflow` switches one off,
  which is the right answer to "stop it".
- You cannot create or read a credential, ever.

## When n8n is not configured

The tools say so and name the setting. Relay that plainly — it is a two-line
setup, not a dead end.
