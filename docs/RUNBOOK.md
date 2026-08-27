# RUNBOOK — bringing Jarvis up, taking it down, and getting the data back

Every command here was run on this host and its output is what is printed. If a
command needs something this box does not have, that is said rather than
implied.

The stack is two compose files, and they are brought up in this order:

| File | What is in it |
|---|---|
| `jarvis-core/docker-compose.yml` | jarvis-core, the three Wyoming voice services, jarvis-browser, and (behind profiles) SearXNG, mosquitto and photon |
| `docker-compose.yml` (repo root) | jarvis-web (the console), and behind `--profile agents`, the orchestrator and the sandbox |

The core file first: the console will start without a backend and show its own
"cannot reach the server" state, which is a worse first impression than waiting
ten seconds.

## Bring it up

```bash
cd jarvis-core && docker compose up -d --wait
cd ..           && docker compose up -d --wait
```

`--wait` is the point: it blocks until every service with a healthcheck reports
**healthy**, and exits non-zero if one does not. That is what
`scripts/verify/live_interaction.sh` uses, and it is why every service in both
files now has a healthcheck — including the three Wyoming ones, which had none
and which are exactly the services the voice path fails on.

Optional services are behind profiles, and each is a deliberate act:

```bash
docker compose --profile search  up -d searxng   # private metasearch, :8888
docker compose --profile mqtt    up -d mosquitto # a broker, :1883
PHOTON_REGION=gb \
docker compose --profile geocode up -d photon    # offline geocoder, :2322
docker compose --profile agents  up -d           # orchestrator + sandbox (root file)
```

**`PHOTON_REGION` is not optional.** With no region the image downloads the
whole-planet index: 58 GB, needing 152 GB of temp space. On this host it
checked the disk, refused, exited, and `restart: unless-stopped` did it again —
**2,699 times over two days**, while every test suite was green. Set a country.

## Check it

```bash
docker compose ps                        # STATUS says (healthy) or it does not
docker compose logs -f --tail 50 jarvis-core
docker compose logs --since 10m | grep -iE '\berror\b'
```

The live suite runs the last of those and fails the run on a hit, which is how
a container that is up but complaining stops being invisible.

## Take it down

```bash
docker compose down                # stop and remove containers; volumes stay
docker compose down --volumes      # ...and delete the named volumes too
```

`down` on its own never touches the bind-mounted state (`config/`, `wyoming/`,
`photon/`) — those are directories on the host and Docker has no opinion about
them. `--volumes` removes `mosquitto-data` and nothing else.

## Back it up, and put it back

Most of Jarvis's state is a bind mount, on purpose: `config/configuration.yaml`
is a file people edit, `config/notes/*.md` are documents they own, and
`config/.storage/*.json` is what "it is your data" means when somebody wants to
grep it. The cost of that choice is that backup is `tar`, not a volume driver.

| What | Where | Matters because |
|---|---|---|
| The house, notes, memory, tokens | `jarvis-core/config/` | irreplaceable |
| STT and TTS models | `jarvis-core/wyoming/` | ~200 MB, re-downloadable, slow |
| Geocoder index | `jarvis-core/photon/` | large, re-downloadable |
| Broker persistence | `mosquitto-data` (named volume) | retained topics |
| Coding sandbox workspace | `jarvis-workspace/` | whatever a job left |

**A directory, backed up and restored:**

```bash
# stop first: a tar of a live SQLite file is a tar of a half-written one
docker compose stop jarvis-core
tar czf ~/jarvis-config-$(date +%F).tgz -C jarvis-core config
docker compose start jarvis-core

# and back
docker compose stop jarvis-core
tar xzf ~/jarvis-config-2026-08-25.tgz -C jarvis-core
docker compose start jarvis-core
```

**A named volume, backed up and restored:**

```bash
docker run --rm -v mosquitto-data:/v -v "$PWD":/out busybox \
  tar czf /out/mosquitto-data.tgz -C /v .

docker run --rm -v mosquitto-data:/v -v "$PWD":/in busybox \
  sh -c 'rm -rf /v/* && tar xzf /in/mosquitto-data.tgz -C /v'
```

The live suite uses exactly these two recipes around any scenario that wipes
memory or clears task history, which is what makes it re-runnable against a
stack somebody actually uses: snapshot, run the destructive scenario, restore.
`testing/live/stack.py` (`VolumeGuard`, `Snapshot`) is the implementation and it shells out
to the same commands; since 27 Aug 2026 a restore leaves the operator's own files —
`configuration.yaml`, the included YAML, packages, agents, models — untouched (`OPERATOR_FILES`).

## Change code and see it run

```bash
docker compose up -d --build jarvis-core     # rebuild one service and restart it
docker compose watch                         # rebuild on save, where supported
```

`watch` needs a `develop:` block on the service. Four have one:
`jarvis-core` and `jarvis-browser` (sync the Python and restart, rebuild when
requirements change), `jarvis-orchestrator` (the same), and `jarvis-web`
(rebuild, because the console is built rather than interpreted — a change under
`src/` has to go through vite before it is anything).

`jarvis-sandbox` deliberately has none: it is a container a coding job runs
inside, one per job, created and destroyed by `jarvis-core` — there is no
long-running process to sync into. The pulled images have none either, and
cannot.

`watch` syncs into the directory each image actually runs from — `/srv` for
the three Python services, `/app` for the console. That is pinned by
`test_every_watch_rule_syncs_into_that_image_workdir`, because the first
version of these blocks synced into `/app` for all four: the file landed in a
directory that does not exist in the image, the service restarted, and it
restarted with the old code. A dev loop that silently does nothing is worse
than none.

## Who owns the config directory

`./config` is a bind mount, so its ownership is the host's. The container
writes `.storage/` (registries, tokens) and the recorder database into it, so
the uid inside has to match the uid outside:

```bash
grep JARVIS_UID .env      # JARVIS_UID=1000 — this checkout's own user
```

Set it to your own `id -u` / `id -g` when the config directory is one you also
edit. The image's own uid (10003) remains the default for anyone who does not
set it, and `jarvis-config-init` chowns the directory to whichever it is on
every `up`. Without this, `configuration.yaml` — a *tracked file in this
repository* — came back owned by uid 10003 and could not be edited or checked
out by the person working on it.

## Run the live suite against this stack

```bash
bash scripts/verify/live_interaction.sh --implemented-only
```

It brings the stack up with `--wait`, refuses to start if any container is
unhealthy, talks to the running jarvis-core and the console on :8199, and fails
at the end if any container logged an ERROR-level record while it ran.

It needs a house to talk about. A fresh Jarvis controls nothing — a default
configuration that invents devices nobody owns would be worse — so on a box
with no hardware attached, drop in the demo house:

```bash
cp jarvis-core/config/examples/house/packages-demo-house.yaml \
   jarvis-core/config/packages/demo-house.yaml
docker compose -f jarvis-core/docker-compose.yml restart jarvis-core
```

Three lights, two switches, a lock, a thermostat and the rest, each going
through the same service calls and tier checks as real hardware. The rig
refuses to run and prints those two lines if it finds nothing controllable,
rather than failing every house scenario on a missing entity.

It is safe to run against a house you use, and deliberately:

* `jarvis-core/config`, `.storage` and the `mosquitto-data` volume are tarred
  into `.verify/live/snapshots/` before the first word and restored after the
  last — the same `docker run … busybox tar` recipe as **Back it up** above.
  Restore means *as it was*: a file that appeared during the run is removed as
  well as a changed one being put back.
* Every thread it opens is named `test:<scenario>:<variant>`, so what the suite
  did is identifiable in your own thread list.
* The `voice` and `text` variants go through the API. A scenario that asserts
  on the page (`ui:` — a testid and the text it must show) declares `voice-ui`
  or `text-ui` instead, and the rig drives the real console on :8199 in a
  headless browser: the WAV is played as the microphone, or the words are
  typed into chat. Those variants need the console up; `--no-browser` skips
  them and says so.
* Anything a scenario creates — notes, memory entries, threads — is deleted at
  the end of that scenario, and its absence is asserted before the next one
  starts. A leftover is a failure, not a warning.
* Two scenarios stop containers on purpose (`resilience-core-restart`,
  `resilience-stt-down`). Both put them back, and the run fails if one does not
  come home.

`--target harness` opts out of all of that and runs against a throwaway
jarvis-core instead, for a machine with no stack up.

## Versions, and changing one

Every image is pinned. The three Wyoming services are pinned to the exact
versions this repository's numbers were measured against — whisper 3.5.0, piper
2.3.1, openwakeword 2.1.0 — because `:latest` meant a recogniser could change
under a word-error-rate threshold and look like a regression in Jarvis.

Upgrading one is a deliberate act with a cost attached:

```bash
# 1. note where you are
python3 -m testing.live.runner --implemented-only --no-browser
# 2. bump the tag in the compose file, then
docker compose up -d --wait wyoming-whisper
# 3. and see what it did to the numbers
python3 -m testing.live.runner --implemented-only --no-browser
```

If the word error rate or the latency got worse, put the tag back. That is the
same contract every service in `docs/TOOLING_DECISIONS.md` is held to.
