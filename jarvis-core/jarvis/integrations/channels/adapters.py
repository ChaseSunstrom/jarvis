"""The three adapters: Telegram, Signal, and the one the tests use.

All three are the same four methods, and none of them opens a port. That is the
design constraint, not an implementation detail — a channel that listens is a
channel somebody else can reach, and this project's whole reason for existing
is that it is not on the public internet.

    Telegram   long-polls `getUpdates` over HTTPS, outbound only.
    Signal     polls a `signal-cli-rest-api` container on the tailnet.
    Memory     an in-process fake, so no test ever touches an account.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class MemoryChannel:
    """A channel that goes nowhere. What the tests and the live rig use.

    Deliberately in the shipped code rather than in `testing/`: the live rig
    drives the REAL hub through it, so what is exercised is the authentication,
    the rate limit, the quarantine and the agent — everything except the wire.
    """

    name = "memory"

    def __init__(self, name: str = "memory") -> None:
        self.name = name
        self.sent: list[dict[str, Any]] = []

    async def send(self, text: str, to: str = "") -> dict[str, Any]:
        self.sent.append({"text": text, "to": to})
        return {"status": "sent", "to": to}

    def identify(self, payload: dict[str, Any]) -> str:
        return str(payload.get("from") or payload.get("sender") or "")

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "kind": "memory", "sent": len(self.sent)}


class TelegramChannel:
    """Telegram's bot API, polled. No webhook, so nothing is exposed."""

    name = "telegram"

    def __init__(self, token: str = "", api: str = TELEGRAM_API, client: Any = None,
                 timeout: float = 30.0) -> None:
        self.token = str(token or "")
        self.api = str(api or TELEGRAM_API).rstrip("/")
        self.timeout = float(timeout)
        self._client = client
        self._offset = 0

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _url(self, method: str) -> str:
        # The token is in the PATH because Telegram's API requires it there.
        # It is therefore never logged: `security/secrets.py` registers it and
        # redacts by value, which is why that redaction is value-based.
        return f"{self.api}/bot{self.token}/{method}"

    async def _call(self, method: str, **params: Any) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("no telegram token configured")
        if self._client is not None:
            answer = await self._client.post(self._url(method), json=params, timeout=self.timeout)
        else:
            import httpx

            async with httpx.AsyncClient(timeout=self.timeout) as http:
                answer = await http.post(self._url(method), json=params)
        answer.raise_for_status()
        return answer.json()

    async def send(self, text: str, to: str = "") -> dict[str, Any]:
        if not to:
            return {"status": "error", "error": "telegram needs a chat id to send to"}
        await self._call("sendMessage", chat_id=to, text=text[:4000])
        return {"status": "sent", "to": to}

    def identify(self, payload: dict[str, Any]) -> str:
        message = payload.get("message") or payload.get("edited_message") or {}
        return str((message.get("from") or {}).get("id") or "")

    async def poll(self) -> list[dict[str, str]]:
        """One `getUpdates`. Returns `[{sender, text}]` for the hub to judge."""
        payload = await self._call("getUpdates", offset=self._offset, timeout=0)
        out: list[dict[str, str]] = []
        for update in payload.get("result") or []:
            self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
            message = update.get("message") or update.get("edited_message") or {}
            text = str(message.get("text") or "")
            sender = self.identify(update)
            if text and sender:
                out.append({"sender": sender, "text": text})
        return out

    async def health(self) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "error": "no token"}
        try:
            me = await self._call("getMe")
        except Exception as err:  # noqa: BLE001 - unreachable is the answer
            return {"ok": False, "error": f"{type(err).__name__}"}
        return {"ok": True, "as": (me.get("result") or {}).get("username")}


class SignalChannel:
    """signal-cli-rest-api, on the tailnet. Also polled, also no webhook."""

    name = "signal"

    def __init__(self, url: str = "", number: str = "", client: Any = None,
                 timeout: float = 30.0) -> None:
        self.url = str(url or "").rstrip("/")
        self.number = str(number or "")
        self.timeout = float(timeout)
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.url and self.number)

    async def _request(self, method: str, path: str, **body: Any) -> Any:
        if not self.configured:
            raise RuntimeError("signal needs both `url:` and `number:`")
        url = f"{self.url}{path}"
        if self._client is not None:
            call = getattr(self._client, method)
            answer = await call(url, json=body or None, timeout=self.timeout)
        else:
            import httpx

            async with httpx.AsyncClient(timeout=self.timeout) as http:
                answer = await getattr(http, method)(url, json=body or None)
        answer.raise_for_status()
        return answer.json()

    async def send(self, text: str, to: str = "") -> dict[str, Any]:
        if not to:
            return {"status": "error", "error": "signal needs a recipient"}
        await self._request("post", "/v2/send", message=text, number=self.number, recipients=[to])
        return {"status": "sent", "to": to}

    def identify(self, payload: dict[str, Any]) -> str:
        envelope = payload.get("envelope") or {}
        return str(envelope.get("source") or envelope.get("sourceNumber") or "")

    async def poll(self) -> list[dict[str, str]]:
        payload = await self._request("get", f"/v1/receive/{self.number}")
        out: list[dict[str, str]] = []
        for entry in payload or []:
            envelope = entry.get("envelope") or {}
            message = (envelope.get("dataMessage") or {}).get("message") or ""
            sender = self.identify(entry)
            if message and sender:
                out.append({"sender": sender, "text": str(message)})
        return out

    async def health(self) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "error": "no url or number"}
        try:
            await self._request("get", "/v1/health")
        except Exception as err:  # noqa: BLE001
            return {"ok": False, "error": f"{type(err).__name__}"}
        return {"ok": True, "as": self.number}
