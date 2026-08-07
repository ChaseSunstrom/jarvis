# jarvis_tools — custom tools in <10 lines of YAML

Adding a tool to Jarvis:

1. Drop a `<name>.tool.yaml` manifest in this directory (see
   `paperless_search.tool.yaml`).
2. `python3 generate_config.py --secrets /config/secrets.yaml`
3. Copy/symlink `ha-config/generated/jarvis_tools.yaml` into your HA packages
   dir and restart HA (or reload scripts + rest_command).
4. Expose the new `script.<name>` to Assist (Settings → Voice assistants →
   Expose, or via `ha-config/generated/jarvis_expose.yaml` with
   `scripts/expose-tools.py`).

## Manifest reference

```yaml
name: paperless_search            # ^[a-z][a-z0-9_]{2,40}$
description: "Search Paperless-ngx documents by query text"   # what the LLM reads
tier: 1                           # 1 free · 2 background-capable · 3 approval-gated
service:
  method: GET                     # GET/POST/PUT/DELETE
  url: "http://host:8000/api/documents/?query={{ query }}"
  headers: { Authorization: !secret paperless_token }
  # payload: '{"q": "{{ query }}"}'      # for POST bodies (template ok)
  # content_type: application/json
  fields:
    query: { description: "search text", required: true }
```

Notes:

* **Secrets** must be the *entire* value (`Authorization: !secret
  paperless_token` where the secret contains `Token abc123...`). HA cannot
  splice a secret into the middle of a string; the generator normalises and
  warns if you try. A manifest whose secret is missing from `secrets.yaml`
  is emitted **commented out** so HA never fails to start on it.
* **Tier 3** tools get the human-approval gate prepended — the generated
  script calls `script.jarvis_request_approval` and stops on anything but an
  explicit approve. The gate lives outside the model; persona wording cannot
  soften it.
* Field names become both the script's typed fields and the Jinja variables
  available in `url` / `payload`.

## MCP servers (the other way to add tools)

For tools that already exist as MCP servers, skip manifests entirely: add the
**Model Context Protocol** integration in HA (Settings → Integrations → MCP,
available since 2025.2) pointing at the server's SSE endpoint; its tools
appear to the Jarvis agent automatically. For stdio-only servers, front them
with `mcp-proxy`. See `docs/architecture.md` §Tools.

## Tool management UX

The HUD's **Tools** page (`/tools` in jarvis-web) lists generated tools,
lets you toggle exposure, test-run one with sample arguments, and view the
last trace — see `jarvis-web/README.md`.
