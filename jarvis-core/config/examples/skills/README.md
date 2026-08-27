# Example skills

Nothing in here is loaded. Copy a folder into `config/skills/` and add
`skills:` to `configuration.yaml`:

```yaml
skills:
  path: skills          # relative to the config directory
```

Each skill is a directory with a `SKILL.md` in it — YAML frontmatter, then a
markdown body, in the open Agent Skills format. `references/`, `scripts/` and
`assets/` may sit beside it; Jarvis reads them and **never runs them**.

See `jarvis-core/docs/skills.md` for the format, what a skill may not do, and
why only the name and description reach the system prompt.
