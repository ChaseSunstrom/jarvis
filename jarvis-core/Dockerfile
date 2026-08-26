# jarvis-core — the whole home-automation + voice platform in one image.
#
# Deliberately boring: a stock Python base, the pure-Python dependency set from
# requirements.txt, and the `jarvis` package. No compiler toolchain, no system
# services, no Home Assistant. Everything that varies between houses lives in
# the mounted /config volume, so upgrading is `docker compose pull && up -d`
# and your configuration is untouched.
FROM python:3.12-slim

# --- system packages (best-effort by design) --------------------------------
# The image needs NOTHING from apt to work: the healthcheck below uses stdlib
# urllib, and every dependency in requirements.txt is a pure-Python wheel. This
# step only buys two conveniences, so it is allowed to fail:
#
#   tzdata  glibc needs it to resolve a named zone. Without it `TZ=Europe/London`
#           silently degrades to UTC, which shifts time-of-day triggers and the
#           sun integration. docker-compose.yml also bind-mounts the host's
#           /etc/localtime, which covers this even when apt is unreachable.
#   curl    for poking the API from inside the container while debugging.
#
# apt defaults to plain HTTP (port 80), which a lot of networks block — the
# usual cause of "Connection timed out" against deb.debian.org. Switch the
# sources to HTTPS first, force IPv4 (a container that picks an IPv6 route to
# the mirror can hang indefinitely), and bound every fetch with a timeout so a
# blocked mirror fails fast into the WARN instead of stalling the build.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null \
        || sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list 2>/dev/null || true; \
    ( timeout 180 apt-get -o Acquire::ForceIPv4=true update \
      && timeout 180 apt-get -o Acquire::ForceIPv4=true install -y \
           --no-install-recommends tzdata curl git \
      && rm -rf /var/lib/apt/lists/* ) \
    || echo "WARN: apt failed/timed out. Voice and the house work; named-timezone resolution (see the /etc/localtime mount in docker-compose.yml), the curl debugging convenience and CODING JOBS are affected — the code integration needs git on this PATH and reports 'git is not installed' without it."

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    JARVIS_CONFIG=/config

WORKDIR /srv

# Dependencies before source: editing an integration re-uses the cached layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY jarvis ./jarvis
# Host build caches (3.11 .pyc in a 3.12 image) are dead weight; drop them.
RUN find /srv/jarvis -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# --- non-root ---------------------------------------------------------------
# /config is a bind mount whose ownership comes from the host, so the container
# cannot chown it. uid 10003 is what the compose file's config dir must be
# writable by — Jarvis writes .storage/ (registries, tokens) and the recorder
# database there. See README "Permissions" if you get EACCES on first run.
RUN useradd --uid 10003 --create-home --shell /usr/sbin/nologin jarvis \
 && mkdir -p /config \
 && chown -R jarvis:jarvis /srv /config
USER 10003

EXPOSE 8080

# Liveness only — /healthz is an open route (no token) that reports whether the
# event loop is serving. Uses the interpreter that is guaranteed present rather
# than curl, which the best-effort apt step above may have skipped.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]

# Exec form: python becomes PID 1 and receives SIGTERM directly, which is what
# `jarvis/__main__.py` installs handlers for — so `docker compose stop` runs the
# orderly shutdown (flush the recorder, save the registries) instead of a kill.
ENTRYPOINT ["python", "-m", "jarvis", "--config", "/config"]
