# jarvis-orchestrator: fan-out + OpenCode + gated execution broker.
# Non-root, read-only rootfs (compose), egress limited by firewall to
# Ollama + HA webhook only (see docs/security.md).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

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
