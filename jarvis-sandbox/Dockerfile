# jarvis-sandbox: the execution jail. Compose adds the real fences
# (network_mode none, read-only, cap_drop ALL, no-new-privileges, limits);
# this image just keeps the inside boring: minimal tools, non-root user.
FROM python:3.12-alpine

# A small useful toolbelt for approved commands; nothing setuid.
# The executor only needs python + /bin/sh, both already in the base image via
# busybox — so the DEFAULT build makes NO network calls and cannot hang on a
# blocked/slow/IPv6-stalled Alpine mirror. Extra convenience tools for approved
# commands (bash, coreutils, git, jq) are opt-in and bounded by a timeout so
# they can never hang the build:
#   docker compose build --build-arg SANDBOX_EXTRA_TOOLS=1 jarvis-sandbox
ARG SANDBOX_EXTRA_TOOLS=0
RUN if [ "$SANDBOX_EXTRA_TOOLS" = "1" ]; then \
      timeout 120 apk add --no-cache bash coreutils git jq \
        || echo "WARN: apk failed/timed out; sandbox will use busybox tools only"; \
    else \
      echo "sandbox: busybox tools only (set --build-arg SANDBOX_EXTRA_TOOLS=1 for bash/coreutils/git/jq)"; \
    fi
RUN find / -perm /6000 -type f -exec chmod a-s {} \; 2>/dev/null || true

RUN adduser -D -u 10001 sandbox
COPY executor.py /executor.py

USER 10001
WORKDIR /workspace
CMD ["python", "/executor.py"]
