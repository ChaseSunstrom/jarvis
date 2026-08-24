# MCP: somebody else's tools, inside Jarvis

An [MCP](https://modelcontextprotocol.io) server lends Jarvis its tools. Point
at one and the model can call them; nothing else about the house changes.

```yaml
mcp:
  default_tier: 2          # what a server's tools register at
  allow_stdio: false       # see "the closed door" below
  servers:
    - name: notes
      url: http://127.0.0.1:9100/mcp
      token: !env_var MCP_NOTES_TOKEN
```

Servers can also be added from the console; those live in
`.storage/mcp_servers.json`. On a name collision **the file wins** — it is the
operator's statement, and a web request does not get to shadow it.

## What arrives, and what it is allowed to do

Every tool is registered as `mcp_<server>_<tool>`, so nothing a server offers
can shadow a built-in. Everything a server *returns* is fenced and marks the
turn untrusted, exactly as a web page is: it is somebody else's text, and it
must not be able to pick an action.

The tier is the server's (`default_tier`, or `tier:` per server) and means what
`tests/contracts/tool_tiers.json` says it means — the table three suites read:

| tier | what it does |
|---|---|
| 1 | runs immediately |
| 2 | runs immediately, and is announced. **It does not ask.** |
| 3 | held until a human says yes, to that exact call |

Tier 2 is the default because an MCP tool is third-party code with side effects
nothing in this process can see: it is worth announcing and not worth stopping
the house for. Set `tier: 3` for a server whose tools should be held.

> The config comment here used to read "2 = confirm first", which tier 2 has
> never done. Somebody reading it and installing a server got tools that ran
> without asking. The contract file exists so that sentence can only be wrong
> in one place, and a test fails when it is.

## The closed door: stdio

An `http` server is a URL Jarvis fetches. A **stdio** server is a program
Jarvis *starts* — `npx -y some-package` — as the jarvis-core user, with that
user's filesystem. So `allow_stdio` lives in `configuration.yaml` and nowhere
else: no request, from the console, a phone or a model, can turn it on.

The console still *shows* the option, disabled, with the reason attached. An
option that is simply absent reads as a missing feature; one that is closed
with a reason reads as a decision.

## Inspect: what it is and why it is not up

`jarvis/mcp/inspect` (or `GET /api/mcp/servers/<name>/inspect`) returns one
server in full:

* `server_info` and `protocol_version` — what it said it was during the
  handshake.
* every tool's **JSON schema**. This is the field the view exists for: when a
  tool call keeps failing, the answer is in the arguments about nine times in
  ten.
* `last_error` — why it is not connected. A server that is simply absent from
  the tool list tells nobody anything.
* `attempts` and `next_attempt_in` — where it is in the reconnect backoff.

The console draws all of it behind the TOOLS/INSPECT button on the Tools page,
with a **test call** beside each tool. That call goes through
`jarvis/tools/call` — the same path and the same approval gate the model uses —
so testing a Tier-3 tool from the console holds it for a human exactly as a
conversation would. A console-only execution path would be a way around the
gate, and the entire argument for the gate is that there is only one.

## Staying connected

A server that was down when Jarvis booted used to stay down until somebody
pressed reconnect. An MCP server in the same compose file starts a few seconds
after jarvis-core roughly every time, so "the extra tools exist once a human
notices" was the actual behaviour.

Now a watcher retries anything that is not connected, with per-server backoff:
30 s, doubling, capped at 30 minutes. A slow starter is picked up in under a
minute; a decommissioned server costs two requests an hour rather than one
every ten seconds for a week.

## Testing

```bash
cd jarvis-core && python3 -m pytest tests/test_mcp.py -q
cd jarvis-web && npx vitest run src/lib/mcpDraft.test.ts
cd jarvis-web && E2E_PORT=8299 npx playwright test e2e/mcp.spec.ts
```

The unit tests run against a scripted transport — a fake server that answers
`initialize`, `tools/list` and `tools/call` — so the client, the namespacing,
the fencing, the tier and the backoff are all proved offline. What is **not**
proved is a real third-party server: `docs/verification.md` says so.
