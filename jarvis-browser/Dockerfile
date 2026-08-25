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

# Chromium's shared libraries, by name.
#
# NOT `playwright install-deps chromium`, which is what this used to be and
# which silently produced an image whose browser could not start. Playwright
# 1.49 does not recognise Debian trixie, falls back to its Ubuntu 20.04 package
# list, and that list still names `ttf-unifont` and `ttf-ubuntu-font-family` —
# gone from trixie. apt fails the WHOLE transaction on an unavailable package,
# so *none* of the libraries were installed, chromium died with
# "libglib-2.0.so.0: cannot open shared object file", and /fetch returned 500
# while /healthz said 200. This host ran that image for weeks.
#
# The list below is Playwright's own, with the two dead font packages replaced
# by `fonts-unifont`. The unsuffixed names resolve to the t64 packages on
# trixie, so it works on both.
RUN ( timeout 300 apt-get -o Acquire::ForceIPv4=true update \
      && timeout 600 apt-get -o Acquire::ForceIPv4=true install -y \
           --no-install-recommends \
           libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 \
           libatk-bridge2.0-0 libatspi2.0-0 libcups2 libdrm2 libgbm1 libegl1 \
           libgtk-3-0 libpango-1.0-0 libcairo2 libx11-6 libx11-xcb1 libxcb1 \
           libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
           libxshmfence1 libxkbcommon0 libasound2 libfontconfig1 libfreetype6 \
           fonts-liberation fonts-unifont \
      && rm -rf /var/lib/apt/lists/* ) \
    || echo "WARN: chromium's libraries could not be installed (apt \
unreachable); /fetch, /crawl, /screenshot and /act will fail until they are." ; \
    ( timeout 900 python -m playwright install chromium \
      || echo "WARN: playwright install chromium failed — the browser is \
missing; /fetch, /crawl, /screenshot and /act will 502 until it is installed." )

# And prove it can actually start, at BUILD time, so an image that cannot
# browse is not something a deployment discovers. Best-effort like everything
# else here — a build with no network never got a browser to test — but when
# there IS one, this is the check that would have caught the trixie breakage.
RUN python - <<'PROBE' || echo "WARN: chromium is present but did not launch."
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page()
        await page.set_content("<h1>ok</h1>")
        assert await page.inner_text("h1") == "ok"
        await browser.close()
    print("chromium launches")

asyncio.run(main())
PROBE

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
