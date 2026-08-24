# Skills: teach it something by putting a folder on disk

A **skill** is a directory with a `SKILL.md` in it, in the open
[Agent Skills](https://code.claude.com/docs/en/skills) format — YAML
frontmatter, then a markdown body. Drop one into `config/skills/` and Jarvis
knows it. No code, no restart beyond `skills.reload`.

```
config/skills/roasting/
  SKILL.md          <- frontmatter + the instructions
  references/       <- longer material the body can point at
  scripts/          <- programs. Jarvis NEVER runs these.
  assets/
```

```markdown
---
name: roasting
description: How this house roasts coffee — times, temperatures, the log.
allowed-tools: [get_state, remember]
metadata:
  owner: kitchen
---

## Roasting

Preheat to 210 °C. First crack at about nine minutes…
```

`name` and `description` are required. Everything else is optional, and any key
the format grows is kept in `metadata` rather than refused.

```yaml
skills:
  path: skills          # relative to the config directory
  max_body_chars: 8000  # a longer body is truncated when handed to the model
  # enabled: [roasting] # load only these; omit for all of them
```

## Progressive disclosure, and why it is not a nicety

Every loaded skill contributes **one line** to the system prompt: its name and
its description. The body arrives only when the model calls `use_skill`.

Twelve skills of two thousand words each would be twenty-four thousand words in
front of every "turn the lights off". The context window fills with
instructions about coffee, the house summary falls off the end, and the
assistant gets worse at everything in exact proportion to how much you have
taught it. The index costs about fifteen words per skill.

So the model's turn looks like this:

1. It sees `- roasting: How this house roasts coffee — times, temperatures, the log.`
2. You ask it to start a roast.
3. It calls `use_skill(name="roasting")` and gets the instructions.
4. It acts on them, through the ordinary tools, at their ordinary tiers.

## What a skill may not do

**It cannot run anything.** `scripts/` beside a `SKILL.md` is material for a
human, or for the gated coding path. This integration reads files and never
executes one. A skill that could run a program would be a shell script
installed by dropping a markdown file in a folder — and the folder is not an
API, it is somebody's Nextcloud sync directory.

**It cannot grant itself tools.** `allowed-tools` *narrows* what the model
should reach for while that skill applies. It can never widen the set, and it
can never lower a tier: `lock_control` is Tier 3 because `llm/tools.py` says
so, and a document listing it stays Tier 3. `tests/test_skills.py` pins this
(`test_a_gated_tool_stays_gated_whatever_a_skill_says`).

**It cannot become structure.** `name` and `description` are collapsed to one
clipped line each, so a description containing newlines and a `## System`
heading cannot forge a section of the system prompt. The text is kept — nothing
is censored — but it arrives as one bullet, which is data.

The difference from `memory`'s rule about untrusted text is worth stating: a
skill is written by the operator, not by a web page. The danger being guarded
against here is a **mistake**, not an attack. A skill that arrived from
somewhere else should be read before it is dropped in, exactly like a shell
script from the internet.

## The API

| Surface | Command |
|---|---|
| Websocket | `jarvis/skills/list`, `jarvis/skills/get`, `jarvis/skills/reload` |
| REST | `GET /api/skills`, `GET /api/skills/<name>`, `POST /api/skills/reload` |
| Services | `skills.list`, `skills.get`, `skills.reload` |
| LLM tool | `use_skill(name)` |

All read-only apart from `reload`, deliberately: a skill is created by putting
a folder on disk. An editor in the console would be a second, worse way to
write files on the server.

The console lists them on **Tools**, beside the MCP servers — "what can it do"
is one question whether the answer is a tool or a document. Skills that could
**not** be loaded are listed too, with the path and the reason, because a
mistyped frontmatter otherwise just makes a skill silently absent.

## Testing

```bash
cd jarvis-core && python3 -m pytest tests/test_skills.py -q
```

An example ships in `config/examples/skills/house-style/`. It is not loaded;
copy it into `config/skills/` to try it.
