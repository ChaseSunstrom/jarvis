---
name: writing-skills
description: Use when the user teaches you how they want something done, corrects you twice on the same kind of task, or asks you to remember a procedure. Explains when a skill is the right answer instead of a memory note, and how to write one that will actually fire later.
license: Apache-2.0
---

# Writing a skill

A skill is a procedure you will be handed again later, automatically, when a
similar request arrives. `create_skill` writes one.

## Skill or memory note?

- **A fact** — "the boiler is in the airing cupboard", "Ana prefers 19°C" —
  is a memory note. Use `remember`.
- **A procedure** — the steps for filing a receipt, the way this household
  wants a shopping list built, which nodes their invoice workflow needs — is
  a skill.

The test: if it is something you would DO rather than something you would
KNOW, it is a skill.

## The description is the whole game

Later, you will not see the skill's instructions. You will see one line: its
name and its description. That line is the only thing that decides whether you
open it.

So write the description as **when to use this**, not what it is:

- Bad: "Instructions for the receipt workflow."
- Good: "Use when the user mentions a receipt, an expense, or a VAT return —
  covers which n8n workflow to use and what to do with the PDF."

Name the words a person would actually say. If the user says "expenses" and
your description only says "receipts", the skill will not fire.

## The body

Write it for yourself-in-six-months, in markdown:

- what the skill is for, in a sentence;
- the steps, in order;
- the decisions and how to make them;
- what NOT to do, and why — this is the part that stops a repeat of whatever
  mistake prompted the skill.

Keep it to what is genuinely reusable. A skill that restates general knowledge
is a skill that costs context and teaches nothing.

## After writing one

Say what you wrote and when it will fire, so the user can correct the
description if it would not have caught their phrasing.
