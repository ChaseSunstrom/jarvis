# Skills

A skill is a folder with a `SKILL.md` in it: YAML frontmatter naming it and
saying **when** to use it, then markdown instructions.

```markdown
---
name: filing-receipts
description: Use when the user mentions a receipt, an expense or a VAT return.
---

# Filing a receipt

1. ...
```

This is Anthropic's Agent Skills format, implemented rather than
approximated — so a skill written for Claude works here, and one written here
works there.

## Why, on a local model

Because the context window is the binding constraint. A local 8B reading forty
tool descriptions and three pages of house rules picks worse than one reading
eight skill names.

So there are two lists, and the split is the whole feature:

* the **catalogue** — every enabled skill's name and one line, in the system
  prompt on every turn. Four shipped skills cost about a thousand characters.
* the **body** — loaded only for the skill the model chose, through
  `open_skill`.

The instructions for filing receipts do not compete with the instructions for
the boiler until somebody says "receipt".

## Where they come from

```
jarvis/skills/builtin/<name>/     shipped with Jarvis
<config>/skills/<name>/           yours, and the ones Jarvis writes
```

A local skill with the same name as a shipped one **wins**, which is how you
override the house conventions Jarvis ships with. The same rule
`configuration.yaml` follows everywhere else.

## The three ways one arrives

### Shipped

`n8n-workflows`, `house-automations`, `coding-jobs`, `writing-skills`. These
are the procedures for Jarvis's own capabilities, written as skills so the
persona does not have to carry them and so you can override any of them.

### Written

`create_skill` (Tier 3) writes one into `<config>/skills/`. This is how Jarvis
learns something your household repeats: it writes the procedure down, and the
next matching turn gets it. It is Tier 3 because a skill persists and shapes
every later turn — the one place a model can write into its own future prompt.

Jarvis has a `writing-skills` skill telling it when to do this and how to word
a description that will actually fire.

### Installed

```yaml
skills:
  sources:
    - anthropics/skills
  install_enabled: false
```

`install_skill` (Tier 3) fetches `owner/repo/path/to/skill`, and **it arrives
switched off**.

That is not caution for its own sake. Everything else in Jarvis that reads
somebody else's text — a web page, an MCP result, a document — is fenced and
marks the turn untrusted, because it is data being quoted. **A skill cannot be
fenced: following it is the point.** There is no version of "install this
skill but do not do what it says".

So the control is a person, three times over: installing needs approval, the
source must be on the allow-list, and the skill does nothing until somebody
reads it in the console and switches it on. Set `install_enabled: true` if you
would rather trust the allow-list and skip the last one.

**Two fetch strategies.** The archive
(`codeload.github.com/.../tar.gz`) gets the whole folder including its
scripts. Plenty of networks — including this project's own container — allow
`raw.githubusercontent.com` and block `codeload`, so there is a fallback that
fetches `SKILL.md` and the files its own body names. That is genuinely partial
and says so: the result carries a `caveat` naming what was not fetched, at
install time rather than at run time.

A 404 from the archive host is *not* retried over raw — it means the
repository or branch is wrong, and a second request would only produce a worse
version of the same message.

## Writing a good description

The description is the only thing the model sees when deciding. Write it as
**when to use this**:

- Bad: `Instructions for the receipt workflow.`
- Good: `Use when the user mentions a receipt, an expense or a VAT return.`

Use the words a person would actually say. If they say "expenses" and your
description only says "receipts", the skill never fires. Jarvis refuses a
description under twelve characters for exactly this reason.

## The console

The **SKILLS** page (`g l`, because `g s` was already settings) lists
everything with where it came from, lets you read a body before switching it on, install
from the allow-list, and remove one. Reading a body is the console's
privilege: the model gets one only by calling `open_skill`, and only for a
skill that is on.

## What is deliberately missing

**No tool deletes a skill.** Same rule as repositories and workflows — the
console can, a person can, the model cannot.

**A skill cannot overwrite a shipped one** by name through `create_skill`;
write the file yourself if you mean to override it.
