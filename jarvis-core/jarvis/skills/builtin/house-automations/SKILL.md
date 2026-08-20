---
name: house-automations
description: Use when the user wants something to happen automatically in the house — at a time, at sunset, when a sensor changes, when someone arrives or leaves. Covers the difference between an automation, a script and a scene, and what to check before creating one.
license: Apache-2.0
---

# Automating the house

For anything inside the house — lights, heating, locks, media, sensors — the
answer is an automation here, not an n8n workflow. n8n is for other people's
services.

## Which one

- **Automation** — "when X happens, do Y". A trigger, optional conditions, and
  actions. This is what almost every request means.
- **Scene** — a set of states to apply at once ("Movie night"). Use when the
  user describes a LOOK rather than an event.
- **Script** — a sequence to run on demand, with no trigger of its own.

## Before you create one

1. `list_entities` — use real entity ids. An automation naming an entity that
   does not exist is one that never fires and never says so.
2. Ask which room or which lights if it is ambiguous. An automation is
   persistent; guessing wrong is worse here than in a one-off action.
3. Check whether one already exists that does nearly this. Two automations
   fighting over the same light is a bad afternoon.

## Triggers worth knowing

- time of day, and sunrise/sunset with an offset;
- a state change, with `to`, `from` and `for` (a duration) — `for` is what
  stops a motion sensor firing forty times;
- someone arriving or leaving.

## After creating one

Say what it will do, when, and to which entities, in one sentence. Then say
how to switch it off. A person who cannot find the off switch for something
you made will not let you make another.
