# jarvis-orchestrator: fan-out + OpenCode + gated execution broker.
# Non-root, read-only rootfs (compose), egress limited by firewall to
# Ollama + HA webhook only (see docs/security.md).
FROM python:3.12-slim

# git is needed for code_task diffs; curl + ca-certificates to fetch OpenCode.
# Switch apt to HTTPS first: apt defaults to plain HTTP (port 80), which many
# networks block — the usual cause of "Connection timed out" on deb.debian.org.
# The whole step is best-effort: if the mirrors stay unreachable the image
# still builds and the core API (delegate, execute gate) works fully; only
# code_task (git/OpenCode) is unavailable until the mirror network is fixed.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null \
        || sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list 2>/dev/null || true; \
    ( apt-get update \
      && apt-get install -y --no-install-recommends git curl ca-certificates \
      && rm -rf /var/lib/apt/lists/* ) \
    || echo "WARN: apt failed (mirror unreachable). Core API works; code_task needs git/curl — fix outbound network to the Debian mirrors and rebuild."

# OpenCode (agentic coding CLI, runs against local Ollama). Pin the version;
# update deliberately (docs/security.md §updates).
ARG OPENCODE_VERSION=0.6.4
RUN curl -fsSL https://opencode.ai/install | VERSION=${OPENCODE_VERSION} bash \
 && install -m 0755 /root/.opencode/bin/opencode /usr/local/bin/opencode \
 || echo "WARNING: opencode install failed — code_task will report errors until installed"

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd -u 10002 -m jarvis
USER 10002

EXPOSE 8188
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8188"]
