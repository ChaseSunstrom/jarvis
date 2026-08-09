# jarvis-browser: sandboxed crawling + browser automation (port 8210).
#
# Base choice: python:3.12-slim + an explicit `playwright install chromium`.
# The alternative is `FROM mcr.microsoft.com/playwright/python:v1.49.1-noble`,
# which ships the browsers and their system libs already — use that if you
# can pull it and want a guaranteed-working chromium. It runs as root by
# default, so you would still want the `useradd`/`USER` lines below.
#
# Every apt/network step here is best-effort with a `|| echo WARN`: this repo
# is built on networks that block the Debian mirrors (see the README's build
# troubleshooting). If chromium cannot be installed the image STILL builds and
# the service still starts — /fetch, /crawl, /screenshot and /act will return
# 502 with a clear message until the browser is present, while the security
# core (auth, SSRF policy, the approval gate) is unaffected.
FROM python:3.12-slim

# Playwright drops browsers here; the service reads PLAYWRIGHT_BROWSERS_PATH
# and uses /opt/pw-browsers/chromium (symlinked below).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# apt defaults to plain HTTP (port 80), which many networks block — the usual
# cause of "Connection timed out" on deb.debian.org. Switch to HTTPS and force
# IPv4 (Docker's IPv6 route to the mirrors commonly stalls forever), then bound
# every fetch with a timeout so a blocked mirror fails fast into the WARN.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null \
        || sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list 2>/dev/null || true; \
    ( timeout 180 apt-get -o Acquire::ForceIPv4=true update \
      && timeout 300 apt-get -o Acquire::ForceIPv4=true install -y \
           --no-install-recommends ca-certificates curl \
      && rm -rf /var/lib/apt/lists/* ) \
    || echo "WARN: apt failed/timed out — TLS roots may be stale."

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    || echo "WARN: pip install failed — fix outbound HTTPS to PyPI and rebuild."

# Chromium + its shared libraries. `install-deps` needs apt; `install` needs
# the Playwright CDN. Both are optional at build time.
RUN ( timeout 600 python -m playwright install-deps chromium \
      || echo "WARN: playwright install-deps failed (apt unreachable)." ) ; \
    ( timeout 900 python -m playwright install chromium \
      || echo "WARN: playwright install chromium failed — the browser is \
missing; /fetch, /crawl, /screenshot and /act will 502 until it is installed." )

# Playwright unpacks to /opt/pw-browsers/chromium-<rev>/chrome-linux/chrome.
# Pin a stable path so BROWSER_EXECUTABLE_PATH is predictable; the service
# falls back to Playwright's own resolution if this symlink is absent.
RUN CHROME="$(find /opt/pw-browsers -type f -name chrome -path '*chrome-linux*' 2>/dev/null | head -n1)"; \
    if [ -n "$CHROME" ]; then \
      ln -sf "$CHROME" /opt/pw-browsers/chromium && \
      echo "chromium -> $CHROME"; \
    else \
      echo "WARN: no chromium binary found under /opt/pw-browsers."; \
    fi

COPY jarvis_browser ./jarvis_browser

# Non-root. Keeping chromium's own sandbox enabled requires unprivileged user
# namespaces in the container; if the browser refuses to start with
# "Operation not permitted", either grant that or set
# BROWSER_CHROMIUM_NO_SANDBOX=1 (documented trade-off in the README).
RUN useradd -u 10003 -m -d /home/jarvis jarvis \
 && mkdir -p /srv/sessions \
 && chown -R 10003:10003 /srv /opt/pw-browsers /home/jarvis
USER 10003
ENV HOME=/home/jarvis \
    BROWSER_SESSION_ROOT=/srv/sessions

EXPOSE 8210

# Every route needs the bearer token, /healthz included — so does the check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request as u; \
r=u.Request('http://127.0.0.1:8210/healthz', \
headers={'Authorization':'Bearer '+os.environ.get('JARVIS_BROWSER_TOKEN','')}); \
exit(0 if u.urlopen(r,timeout=4).status==200 else 1)" || exit 1

CMD ["uvicorn", "jarvis_browser.app:app", "--host", "0.0.0.0", "--port", "8210"]
