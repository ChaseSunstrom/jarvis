"""The three HTTP calls enrolment makes, on `urllib` alone.

Same rule as `actions/net.py`: no third-party HTTP dependency, because this
agent's only hard dependency is its websocket client and enrolment is not a
reason to grow a second one.

    GET  /api/voice/speaker                     whose voice is enrolled
    POST /api/voice/speaker/enrol?rate=&width=  one sample, raw PCM
    DELETE /api/voice/speaker                   forget it

The URL is derived from the agent's own `server_url`, which is a `ws://` one —
so the scheme is mapped back and the `/api/websocket` path replaced. Deriving
it rather than asking for it again is what stops somebody enrolling into a
different Jarvis from the one this agent is paired with.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlparse, urlunparse

if TYPE_CHECKING:  # pragma: no cover
    from .config import Config
    from .enrol import Sample

_LOGGER = logging.getLogger(__name__)

__all__ = ["SpeakerClient", "SpeakerError", "http_base"]

TIMEOUT = 30.0
#: A sample is a few hundred kilobytes at most; anything larger is a mistake
#: somewhere upstream and should not be sent at all.
MAX_UPLOAD_BYTES = 4_000_000


class SpeakerError(RuntimeError):
    """Something to print, never a traceback."""


def http_base(server_url: str) -> str:
    """`ws://host:8080/api/websocket` -> `http://host:8080`.

    Derived rather than configured. A separate setting for "the HTTP address"
    is a second thing to get wrong, and getting it wrong means enrolling your
    voice into a Jarvis that is not the one this agent talks to.
    """
    parsed = urlparse(server_url or "")
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    if scheme not in ("http", "https") or not parsed.netloc:
        raise SpeakerError(
            f"cannot work out the HTTP address from {server_url!r}; set server_url first"
        )
    return urlunparse((scheme, parsed.netloc, "", "", "", ""))


@dataclass
class SpeakerClient:
    base: str
    token: str
    #: Injected so a test can answer without a server.
    opener: Any = None

    @classmethod
    def from_config(cls, config: "Config") -> "SpeakerClient":
        if not config.token:
            raise SpeakerError(
                "no access token. Set one in config.json, or JARVIS_TOKEN, "
                "before enrolling."
            )
        return cls(base=http_base(config.server_url), token=config.token)

    # --- calls ------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return self._call("GET", "/api/voice/speaker")

    def enrol(self, sample: "Sample") -> dict[str, Any]:
        """Send one sample as raw PCM, at the rate it really is.

        The rate travels with the audio rather than being assumed: a recorder
        asked for 16 kHz on a device that cannot do it hands back 48 kHz, and a
        profile built from audio at a declared rate it is not at matches
        nobody.
        """
        if len(sample.pcm) > MAX_UPLOAD_BYTES:
            raise SpeakerError("that sample is far larger than a voice sample should be")
        query = urlencode({"rate": sample.rate, "width": 2})
        return self._call(
            "POST",
            f"/api/voice/speaker/enrol?{query}",
            body=sample.pcm,
            content_type="application/octet-stream",
        )

    def forget(self) -> dict[str, Any]:
        return self._call("DELETE", "/api/voice/speaker")

    # --- plumbing ---------------------------------------------------------
    def _call(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        content_type: str = "",
    ) -> dict[str, Any]:
        request = urllib.request.Request(f"{self.base}{path}", data=body, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        if content_type:
            request.add_header("Content-Type", content_type)
        opener = self.opener or urllib.request.urlopen
        try:
            with opener(request, timeout=TIMEOUT) as response:
                raw = response.read()
        except urllib.error.HTTPError as err:
            raise SpeakerError(self._explain(err)) from err
        except urllib.error.URLError as err:
            raise SpeakerError(f"could not reach {self.base}: {err.reason}") from err
        except OSError as err:
            raise SpeakerError(f"could not reach {self.base}: {err}") from err
        try:
            return json.loads(raw.decode("utf-8", "replace")) or {}
        except ValueError:
            return {}

    @staticmethod
    def _explain(err: urllib.error.HTTPError) -> str:
        """jarvis-core's own words where it gave any.

        Its refusals are written for a person to act on — "that sample has no
        measurable pitch, it is too quiet" — and replacing them with "HTTP 400"
        throws away the only actionable part.
        """
        detail = ""
        try:
            payload = json.loads(err.read().decode("utf-8", "replace"))
            if isinstance(payload, dict):
                detail = str(payload.get("detail") or payload.get("message") or "")
        except Exception:  # noqa: BLE001 - an error page is not JSON
            detail = ""
        if detail:
            return detail
        if err.code == 401:
            return "the server refused the token; check it in config.json"
        if err.code == 404:
            return "this server has no speaker endpoints — is it jarvis-core?"
        return f"the server answered {err.code}"
