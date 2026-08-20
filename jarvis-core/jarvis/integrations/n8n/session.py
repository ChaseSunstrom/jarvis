"""A logged-in n8n session, for the half of n8n the API key cannot reach.

## Why this exists at all

n8n has two HTTP surfaces and they do not overlap:

    /api/v1/...   the public API.  Authenticated by `X-N8N-API-KEY`.
    /rest/...     what the n8n UI itself talks to.  Authenticated by a
                  session cookie, and by nothing else.

Everything Jarvis does today lives on the first one, deliberately. But three
things Jarvis wants are only on the second: the instance's settings (which is
where it says whether the AI builder is licensed), the node type catalogue
(which is how a model stops inventing node names), and the AI builder itself.
An API key cannot open any of them — `x-n8n-api-key` is read in exactly one
place in n8n and that place is only reachable from the public-API router.

## What a session costs, said plainly

**An n8n password is strictly more powerful than an n8n API key**, and the
asymmetry runs one way. A session cookie also authenticates `/api/v1`, while an
API key never authenticates `/rest`. The `/rest` surface includes minting API
keys. So a login is not "the same access by another route" — it is a superset
of everything the key could do, plus the ability to grant itself more.

Therefore: **use a dedicated non-owner n8n user for Jarvis**, and configure it
in `configuration.yaml` or the environment only. Nothing here is settable over
the API, for the reason the console already gives about the API key — a setting
a request can write is a setting a stolen session can write.

## Four things that are easy to get wrong

**The cookie is managed by hand, not by httpx's jar.** `N8N_SECURE_COOKIE`
defaults to `true`, so an n8n on plain http still sends `Secure` on the
Set-Cookie, and a standards-respecting jar will then refuse to send it back
over http. That failure looks exactly like a wrong password, which is the
worst kind of bug to hand somebody.

**No `browser-id` header, ever.** n8n stamps the session with a hash of that
header at login and checks it on every later request. Omitting it at login
leaves the field unset and the check a permanent no-op. Sending it once means
every subsequent request must carry the byte-identical string, and a mismatch
is a 401 nobody can diagnose.

**The server rotates the token mid-session**, once inside the refresh window.
So `Set-Cookie` is honoured on ORDINARY responses, not only on the login.

**The User-Agent must not contain the substring `bot`.** n8n has a global
filter that answers 204 with an empty body to anything that does — a silently
empty success, which parses as "no data" everywhere downstream.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "N8nSession",
    "SessionError",
    "COOKIE_NAME",
    "DEFAULT_REST_PATH",
    "USER_AGENT",
]

COOKIE_NAME = "n8n-auth"
DEFAULT_REST_PATH = "/rest"
#: Pinned by a test. `bot` anywhere in this string and n8n answers 204 with an
#: empty body to every request.
USER_AGENT = "jarvis-home-assistant"

#: n8n's own default session is 168h and it rotates inside the last 25%. This
#: is only a hint for when to pre-emptively re-login; the authority is the 401.
ASSUMED_SESSION_SECONDS = 60 * 60 * 24 * 5

#: n8n rate-limits login to 5 per minute per email in production. A retry storm
#: locks Jarvis out of its own instance, so there is exactly one retry.
LOGIN_RETRIES = 1


class SessionError(RuntimeError):
    """Anything that went wrong logging in, already phrased for a person."""


class N8nSession:
    """One logged-in user on one n8n instance.

    Holds the cookie in memory and nowhere else. Never written to `.storage`:
    it is a bearer credential for the whole instance, and it is cheap to mint
    again from a password that already has to be in the config.
    """

    def __init__(
        self,
        url: str,
        email: str = "",
        password: str = "",
        *,
        mfa_code: str = "",
        rest_path: str = DEFAULT_REST_PATH,
        timeout: float = 30.0,
        transport: Any = None,
    ) -> None:
        self.url = str(url or "").rstrip("/")
        self.email = str(email or "").strip()
        self.password = str(password or "")
        self.mfa_code = str(mfa_code or "").strip()
        self.rest_path = "/" + str(rest_path or DEFAULT_REST_PATH).strip("/")
        self.timeout = float(timeout)
        self._transport = transport
        self._token = ""
        self._logged_in_at = 0.0

    # --- what the rest of the integration asks --------------------------
    @property
    def configured(self) -> bool:
        return bool(self.url and self.email and self.password)

    @property
    def why_not(self) -> str:
        """Why `/rest` is closed, as a sentence, or ""."""
        if not self.url:
            return "No n8n URL is configured."
        if not self.email or not self.password:
            return (
                "Jarvis has no n8n login, only an API key. The parts of n8n "
                "that need one — the settings, the node catalogue, the AI "
                "builder — are on `/rest`, and an API key cannot open it. Set "
                "`n8n: login: email:` and `password:` (or N8N_LOGIN_EMAIL and "
                "N8N_LOGIN_PASSWORD). Use a dedicated non-owner n8n user: a "
                "login can do everything the API key can and more."
            )
        return ""

    def scrub(self, text: Any) -> str:
        """The password and the cookie never appear in something Jarvis quotes.

        Same three hops as the API key: httpx quotes the request in its
        exceptions, the integration quotes the exception in a tool result, and
        a tool result is read by the model and drawn in the console.
        """
        said = str(text or "")
        for secret in (self.password, self._token):
            if secret and len(secret) >= 8:
                said = said.replace(secret, "***")
        return said

    # --- logging in -------------------------------------------------------
    async def login(self, client: httpx.AsyncClient | None = None) -> str:
        """Mint a session cookie. Returns the token; raises `SessionError`."""
        problem = self.why_not
        if problem:
            raise SessionError(problem)

        body: dict[str, Any] = {
            # n8n's DTO calls it this, not `email`. An `email` key is dropped
            # by validation and the login fails as "wrong password".
            "emailOrLdapLoginId": self.email,
            "password": self.password,
        }
        if self.mfa_code:
            body["mfaCode"] = self.mfa_code

        if client is None:
            async with self._client() as fresh:
                response = await self._post_login(fresh, body)
        else:
            response = await self._post_login(client, body)

        if response.status_code == 401:
            raise SessionError(self._why_401(response))
        if response.status_code == 404:
            raise SessionError(
                f"n8n at {self.url} has no {self.rest_path}/login. Either the "
                "URL is not an n8n, or the REST prefix is not the default — "
                "set `n8n: rest_path:` to match N8N_ENDPOINT_REST."
            )
        if response.status_code == 204 and not response.content:
            raise SessionError(
                "n8n answered 204 with an empty body, which is what its bot "
                "filter does. That should be impossible from here — the "
                "User-Agent is checked by a test — so something in front of "
                "n8n is rewriting it."
            )
        if response.status_code >= 400:
            raise SessionError(
                f"n8n refused the login with {response.status_code}: "
                f"{self.scrub(response.text)[:300]}"
            )

        token = self._token_from(response)
        if not token:
            raise SessionError(
                "n8n accepted the login but set no session cookie. If it is "
                "behind a proxy, check that the proxy is not stripping "
                "Set-Cookie."
            )
        self._token = token
        self._logged_in_at = time.time()
        _LOGGER.info("Logged in to n8n at %s as %s", self.url, self.email)
        return token

    async def _post_login(
        self, client: httpx.AsyncClient, body: dict[str, Any]
    ) -> httpx.Response:
        try:
            return await client.post(
                f"{self.url}{self.rest_path}/login",
                json=body,
                headers=self._headers(),
            )
        except httpx.TimeoutException:
            raise SessionError(
                f"n8n at {self.url} did not answer the login within "
                f"{self.timeout:.0f}s."
            ) from None
        except httpx.HTTPError as err:
            raise SessionError(
                f"could not reach n8n at {self.url}: {self.scrub(err)}"
            ) from None

    def _why_401(self, response: httpx.Response) -> str:
        """A refused login, told apart by n8n's own code."""
        code = 0
        try:
            payload = response.json()
            if isinstance(payload, dict):
                code = int(payload.get("code") or 0)
        except (ValueError, TypeError):
            payload = {}
        if code == 998 or "mfa" in self.scrub(response.text).lower():
            return (
                "This n8n account has two-factor authentication on. Set "
                "`n8n: login: mfa_code:` — but note a TOTP code expires in "
                "thirty seconds, so a config file is a poor place for one. A "
                "dedicated Jarvis user without 2FA is the workable answer."
            )
        return (
            "n8n refused the login (401). Check `n8n: login: email:` and "
            "`password:`. Jarvis will not retry on its own: n8n allows five "
            "login attempts per minute per address, and a retry loop locks "
            "the account out of its own instance."
        )

    # --- using it ---------------------------------------------------------
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """One `/rest` call, logging in first and once more on a 401.

        Returns the raw response: some callers want JSON, one wants to stream
        bytes, and a helper that decided for both would be in the way of the
        one that matters.
        """
        problem = self.why_not
        if problem:
            raise SessionError(problem)

        async with self._client() as client:
            if not self._token:
                await self.login(client)
            for attempt in range(LOGIN_RETRIES + 1):
                response = await self._send(client, method, path, params, json_body, headers)
                self._absorb(response)
                if response.status_code != 401 or attempt == LOGIN_RETRIES:
                    return response
                # A session dies on a password change, an email change, a
                # disabled user, or a logout elsewhere. One re-login, then the
                # 401 is the answer.
                _LOGGER.info("n8n session was rejected; logging in again")
                self._token = ""
                await self.login(client)
            return response

    async def _send(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json_body: Any,
        headers: dict[str, str] | None,
    ) -> httpx.Response:
        target = f"{self.url}{self.rest_path}/{str(path).lstrip('/')}"
        try:
            return await client.request(
                method,
                target,
                params=params,
                json=json_body,
                headers={**self._headers(with_cookie=True), **(headers or {})},
            )
        except httpx.TimeoutException:
            raise SessionError(
                f"n8n at {self.url} did not answer {method} {path} within "
                f"{self.timeout:.0f}s."
            ) from None
        except httpx.HTTPError as err:
            raise SessionError(
                f"could not reach n8n at {self.url}: {self.scrub(err)}"
            ) from None

    def _client(self) -> httpx.AsyncClient:
        # `cookies={}` and never the default jar: see the module docstring.
        # A jar would swallow a `Secure` cookie on a plain-http instance and
        # the symptom would be an unexplainable 401.
        return httpx.AsyncClient(
            timeout=self.timeout,
            transport=self._transport,
            cookies=None,
            follow_redirects=False,
        )

    def _headers(self, *, with_cookie: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            # Never anything containing "bot". See the module docstring.
            "User-Agent": USER_AGENT,
        }
        if with_cookie and self._token:
            headers["Cookie"] = f"{COOKIE_NAME}={self._token}"
        return headers

    def _absorb(self, response: httpx.Response) -> None:
        """Take a rotated token off an ordinary response."""
        token = self._token_from(response)
        if token and token != self._token:
            _LOGGER.debug("n8n rotated the session cookie")
            self._token = token
            self._logged_in_at = time.time()

    def _token_from(self, response: httpx.Response) -> str:
        """Read `n8n-auth` out of Set-Cookie, by hand.

        `response.cookies` goes through httpx's jar, which applies the `Secure`
        rule; over plain http the cookie is simply not there. The raw header
        always is.
        """
        for raw in response.headers.get_list("set-cookie"):
            first = str(raw).split(";", 1)[0].strip()
            name, _, value = first.partition("=")
            if name.strip() == COOKIE_NAME and value.strip():
                return value.strip()
        return ""

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def forget(self) -> None:
        """Drop the cookie. Used when a 403 says the feature is gone anyway."""
        self._token = ""
