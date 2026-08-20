# Worked examples

Nothing in here is loaded. The shipped `config/` boots into an empty house on
purpose — a console full of rooms you do not have and lights you cannot switch
on is worse than an empty one, because you cannot tell the difference between
"not set up yet" and "set up wrong".

This directory is where the old default went, intact, so it is still there when
you want to read it or run it.

## `house/` — the full fake house

Lights, switches, sensors, a thermostat, covers, a lock, a fan, a speaker and a
vacuum across three areas, plus eight automations, five scripts, scenes,
template sensors, input helpers and a person. Every entity implements the real
method contract, so `light.turn_on`, `cover.set_cover_position` and friends
behave exactly as they would against hardware.

Useful for two things: seeing how a real configuration fits together, and
having something to talk to before any hardware arrives.

To switch it on, from `config/`:

```bash
cp examples/house/configuration.yaml examples/house/automations.yaml \
   examples/house/scripts.yaml examples/house/scenes.yaml .
cp examples/house/example.tool.yaml tools/
cp examples/house/packages-laundry.yaml packages/laundry.yaml
```

To switch it off again, `git checkout config/` — or delete the `demo:` block
from `configuration.yaml`, which removes the devices and leaves everything that
referenced them broken, which is exactly why it ships as a set rather than as a
default.

### What is in it

| file | what it adds |
|---|---|
| `configuration.yaml` | `demo:` devices, six areas, template sensors, a person, input helpers, and an `llm.expose` list naming real entity ids |
| `automations.yaml` | motion light, evening wind-down, night mode, door-opened-while-away, high power draw, guest-mode expiry, startup notice, phone location webhook |
| `scripts.yaml` | `goodnight`, `good_morning`, `movie_time`, `announce`, `house_status` — every one carries a `description:`, so each is offered to the model as `script_<name>`; `announce` and `house_status` also declare `fields:` and a `stop:` response |
| `scenes.yaml` | named states spanning lights, covers, locks and climate |
| `packages-laundry.yaml` | one self-contained feature — helper, template sensor, automation and script in one file. Copy to `packages/laundry.yaml` |
| `example.tool.yaml` | a YAML-defined LLM tool. Copy to `tools/` |

### It is also the test fixture

`tests/test_packaging.py` boots this directory to check properties that need a
populated house: that every entity id and service named in an automation
exists, that a scene applies, that a script with metadata becomes a tool, and —
the ones that matter most — that an excluded entity cannot be reached through a
model-runnable script or scene, and that no such macro reaches a gated domain.

Those are safety properties, and they need something to be unsafe *about*. That
is the real reason this bundle is kept whole rather than deleted: an empty house
proves nothing about what happens in a full one.
