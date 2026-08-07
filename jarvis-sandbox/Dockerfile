# jarvis-sandbox: the execution jail. Compose adds the real fences
# (network_mode none, read-only, cap_drop ALL, no-new-privileges, limits);
# this image just keeps the inside boring: minimal tools, non-root user.
FROM python:3.12-alpine

# A small useful toolbelt for approved commands; nothing setuid.
RUN apk add --no-cache bash coreutils git jq \
 && find / -perm /6000 -type f -exec chmod a-s {} \; 2>/dev/null || true

RUN adduser -D -u 10001 sandbox
COPY executor.py /executor.py

USER 10001
WORKDIR /workspace
CMD ["python", "/executor.py"]
