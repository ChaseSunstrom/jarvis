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

## Run what already exists before you build anything

Scripts and scenes are in your prompt with everything else — `script.goodnight`,
`scene.movie_night`. If one is there, **run it**. Do not compose six service
calls that do nearly the same thing: the script is what somebody tuned, and
your composition is a guess that looks identical right up until it is not.

A script the household wrote a description for is **its own tool**, called
`script_<name>` — with its own arguments, and it hands back whatever the
script reports. Look for one before you look for anything else. For the rest,
`run_script` takes the name.

One call, the same order every time, and it finishes faster than you can
reason out the sequence.

## Offer to save a routine the second time

If the same sequence of actions has been asked for twice, say so and offer to
make it a script. That is the whole "consistent and fast" argument: one call
instead of six, the same order every time, and a name the household can say
out loud.

Give it a `description:` when you write it, because that is what turns it
into a tool you can call next time rather than a routine somebody has to
remember. And `fields:` for anything that varies — a goodnight that takes
`delay_minutes` is one script instead of three.

Do not make one unasked. A script is persistent and shows up in the house's
own list; adding one nobody agreed to is clutter with your name on it.

## Twelve worked routines, beside this file

`open_skill` with `file: "recipes.md"` gets you the ones worth having —
goodnight, leaving and arriving, announce, a status script, motion lights,
something-left-on, away-and-something-moved, an appliance finishing, evening
wind-down, an expiring guest mode — each with real YAML and, more usefully,
the trap in that particular shape.

Those traps are the reason to read it: `mode: restart` is what makes a motion
timer extend instead of stack, `sun` is not a trigger platform, a washing
machine drops below five watts several times mid-cycle, and an away-alert
without its condition fires every time somebody comes home.

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
