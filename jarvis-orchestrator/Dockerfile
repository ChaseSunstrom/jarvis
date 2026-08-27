# jarvis-orchestrator: fan-out + OpenCode + gated execution broker.
# Non-root, read-only rootfs (compose), egress limited by firewall to
# Ollama + HA webhook only (see docs/security.md).
FROM python:3.12-slim

# git is needed for code_task diffs; curl + ca-certificates to fetch OpenCode,
# and unzip to unpack it — the installer refuses without it, and for weeks
# this image built with the WARN below swallowed and every code_task answered
# "opencode binary not installed" (the operator's stuck React app, 26 Aug
# 2026, M82).
# Switch apt to HTTPS first: apt defaults to plain HTTP (port 80), which many
# networks block — the usual cause of "Connection timed out" on deb.debian.org.
# The whole step is best-effort: if the mirrors stay unreachable the image
# still builds and the core API (delegate, execute gate) works fully; only
# code_task (git/OpenCode) is unavailable until the mirror network is fixed.
# Fifteen minutes each, not three: on 26 Aug 2026 the 9.6 MB package list
# alone took over two minutes on this network, the three-minute budget
# killed apt, the WARN below swallowed it, and the image shipped with no
# curl — so OpenCode's installer failed too and every code_task answered
# "opencode binary not installed" (M82).
# ForceIPv4 avoids the common Docker IPv6 stall (container tries an IPv6 route
# to the mirror and hangs forever); the timeouts bound every fetch so a blocked
# mirror fails fast into the WARN instead of hanging the build.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null \
        || sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list 2>/dev/null || true; \
    ( timeout 900 apt-get -o Acquire::ForceIPv4=true update \
      && timeout 900 apt-get -o Acquire::ForceIPv4=true install -y \
           --no-install-recommends git curl ca-certificates unzip \
      && rm -rf /var/lib/apt/lists/* ) \
    || echo "WARN: apt failed/timed out. Core API works; code_task needs git/curl — fix outbound network to the Debian mirrors and rebuild."

# OpenCode (agentic coding CLI, runs against local Ollama). Pin the version;
# update deliberately (docs/security.md §updates).
# 1.18.23 (27 Aug 2026): the project moved to github.com/anomalyco/opencode and
# the installer's asset for the old 0.6.4 tag no longer unpacks — the build's
# `|| echo WARNING` had been hiding that since the mirror came back.
ARG OPENCODE_VERSION=1.18.23
RUN curl -fsSL https://opencode.ai/install | VERSION=${OPENCODE_VERSION} bash \
 && install -m 0755 /root/.opencode/bin/opencode /usr/local/bin/opencode \
 && /usr/local/bin/opencode --version \
 || echo "WARNING: opencode install failed — code_task will report errors until installed"

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd -u 10002 -m jarvis
USER 10002
# The image's root is read-only and 10002 has no home in it: OpenCode writes
# under HOME (its config, its cache), so every process — the app, a `docker
# exec` probe, the M82 gate — agrees on the tmpfs (M101).
ENV HOME=/tmp/home

EXPOSE 8188
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8188"]
