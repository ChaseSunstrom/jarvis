---
name: adding-tools
description: Use when a request needs a capability you do not have — an HTTP API nobody wired up, a service on the network, a webhook. Covers when a new tool is the right answer, when an n8n workflow is, and what a tool costs the household.
license: Apache-2.0
---

# When you do not have a tool for it

Three answers, in the order to consider them.

## 1. Is it already there?

Check what you actually have before saying you cannot. Between the house's
own services, n8n, the web tools and any MCP servers the household has
connected, most requests are covered by something already registered.

## 2. Is it an n8n workflow?

Almost always, if the request involves somebody else's service — email,
calendars, spreadsheets, Notion, Slack, invoicing, payments. n8n has hundreds
of maintained connectors and holds the credentials where they belong. See the
`n8n-workflows` skill; do not hand-roll an HTTP call to a service n8n already
has a node for.

## 3. A new tool, for a plain HTTP endpoint

`create_tool` registers a reusable HTTP call: a URL, a method, and named
parameters that go into the path, the query or the body. It is the right
answer for something local and specific — a printer's status endpoint, a
homegrown API on the network, a webhook the household owns.

It is **Tier 3**, which is the interesting part.

## What a tool costs, and why you say so

A tool you create is permanent and available on every future turn, to every
future request. That is the point and it is also the risk: you are not asking
for permission to make one call, you are asking for a capability.

So when you propose one, say plainly:

- what URL it will call, and with what method
- what it will send — and specifically whether anything sensitive goes in it
- that it will exist afterwards, for anything to use

A secret goes in as an `!env_var` reference in the operator's configuration,
never as a literal in the tool. If a request needs an API key you have been
given in conversation, that is the wrong shape: say so and let the operator
put it in the environment.

## When not to make one

- **For one call.** If it will never be used again, it is not a tool; do the
  thing another way or say you cannot.
- **To get around a refusal.** A tool that wraps something you were not
  allowed to do is the same action with a different name, and building one is
  worse than the original request.
- **For a service n8n covers.** See above.
