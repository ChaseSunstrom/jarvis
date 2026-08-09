# SearXNG

The search engine behind `web.search`. It is a *metasearch* engine: it asks
DuckDuckGo, Brave, Mojeek and the rest on your behalf and merges the answers,
so the upstream engines see the container's IP and a bare query with no cookie,
no account and no history. It is the only thing in this stack that talks to the
internet at runtime.

Optional. Jarvis runs perfectly well without it — `web.search` just fails with
an explanation, which is deliberate. It will never quietly fall back to Google.

## Start it

```bash
# 1. Mint a secret key. SearXNG refuses to start without one.
grep -q '^SEARXNG_SECRET=' .env 2>/dev/null || \
  echo "SEARXNG_SECRET=$(openssl rand -hex 32)" >> .env

# 2. Bring the stack up with the search profile.
docker compose --profile search up -d
```

`--profile search` is the whole opt-in. Plain `docker compose up -d` starts
everything else and leaves SearXNG alone, which is what you want if you already
run one somewhere on the LAN — point `SEARXNG_URL` at it instead:

```bash
SEARXNG_URL=http://192.168.1.10:8888 docker compose up -d
```

Check it:

```bash
curl -s 'http://127.0.0.1:8888/search?q=test&format=json' | head -c 200
```

A JSON object means it works. A 403 means `json` is missing from
`search.formats` — see below. Anything else, `docker compose logs searxng`.

## The two settings that are not taste

**`search.formats` must include `json`.** SearXNG serves HTML only unless told
otherwise, and a JSON request against an instance without it comes back **403**,
not a downgrade to HTML. This is the single most common reason `web.search`
fails on a fresh instance, and the error message Jarvis prints says so. It is
off by default upstream because a JSON API turns an instance into something
worth scraping — which is an argument for keeping this one off the public
internet, not for leaving the format off.

**`server.limiter` is off.** The limiter stops strangers scraping a public
instance. This one answers loopback and one LAN host, so all it can do is
rate-limit *you* into HTTP 429 in the middle of a conversation — and it wants a
Valkey/Redis container the stack does not otherwise run.

## The secret key

`settings.yml` ships with the upstream sentinel `secret_key: "ultrasecretkey"`.
SearXNG refuses to start while it still says that, which is the point: a
missing key fails loudly instead of running on a value that is in every copy of
this repository.

`SEARXNG_SECRET` in the environment overrides the file, so nothing secret is
ever written into a tracked file. Generate it once with the one-liner above.
Rotating it only logs everyone out of a UI nobody uses.

## Files

| file | what |
|---|---|
| `settings.yml` | the instance configuration, mounted at `/etc/searxng/settings.yml` |
| `uwsgi.ini` | *(absent — the image's own is used)* |

The container writes nothing here that you need to keep. Deleting the whole
directory and re-pulling loses only what is in `settings.yml`.

## Engines

`use_default_settings: engines: keep_only:` in `settings.yml` narrows the
default engine set to twelve that answer without an API key. Add or remove
names there; anything you list that SearXNG does not have is ignored rather
than an error, so a typo shows up as an engine that never returns results.

Google is deliberately absent. It blocks datacentre scrapers hard, and an
engine that silently returns nothing is worse than one that is not listed.

## Privacy notes

* `enable_metrics: false` — no `/stats`, no per-engine timing histograms.
  Those are local-only, but the useful ones are a query log by another name.
* `image_proxy: false` — Jarvis reads text. Proxying thumbnails would make
  SearXNG fetch every image on every results page.
* The checker (background engine prober) is left unscheduled.
* `bind_address: 127.0.0.1` — the stack uses host networking, so this is what
  decides who can reach SearXNG. Widen it only if another machine needs it, and
  then let ufw say which machine.

## Talking to it from Jarvis

`config/configuration.yaml`:

```yaml
web:
  searxng_url: !env_var SEARXNG_URL http://127.0.0.1:8888
  safe_search: 1
```

Jarvis calls `GET $SEARXNG_URL/search?q=…&format=json&safesearch=…&language=…`
and returns title/url/snippet **fenced as untrusted content**. A search result
is text an attacker chose — anyone can rank for a query — so it is data the
model reads, never instructions it follows. See `../docs/search.md`.
