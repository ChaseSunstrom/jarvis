"""The n8n public API, as much of it as Jarvis needs.

## Four calls and a probe

    GET    /api/v1/workflows            list
    GET    /api/v1/workflows/{id}       one, in full
    POST   /api/v1/workflows            create
    POST   /api/v1/workflows/{id}/activate | /deactivate

plus `GET /api/v1/executions` for "did it work", and `probe()`, which exists
because of the paragraph below.

## Why `probe()` exists

n8n's public API has moved between versions — paths, which fields are
read-only on create, whether a body is accepted at all — and this client was
written against documentation rather than against a live instance. A wrong
guess must therefore be visible immediately and precisely, not turn into "the
workflow list is empty" three days later.

So `probe()` makes the smallest real request there is and reports exactly what
came back: the status, whether the body parsed, and how many workflows were
in it. An operator who points Jarvis at a version this does not fit sees the
mismatch in one sentence on the console, which is the difference between a bug
report and a shrug.

## The key

`X-N8N-API-KEY`, and it never leaves this module. It is not in the argv (there
is no argv — this is httpx), not in a URL, and `redact()` scrubs it from every
error string before that string goes anywhere, because an httpx error quotes
the request and the console quotes the error.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

__all__ = ["N8nError", "N8nClient", "redact", "DEFAULT_TIMEOUT", "MAX_PAGE"]

DEFAULT_TIMEOUT = 30.0
#: One page. n8n's own maximum is 250; asking for more is an error, not a
#: bigger page.
MAX_PAGE = 100

API_PREFIX = "/api/v1"
KEY_HEADER = "X-N8N-API-KEY"


class N8nError(RuntimeError):
    """Anything that went wrong, already phrased for a person."""


def redact(text: Any, *secrets: str) -> str:
    """No credential appears in something Jarvis quotes back.

    httpx puts the request in its exception messages, the integration puts the
    exception in a tool result, and a tool result is read by the model and
    shown in the console. Three hops from a header to a transcript.

    Takes all of them, not only the API key, because there are three now: the
    key, the login password, and the session cookie. The cookie is the worst
    of the three to leak — it is a bearer credential for the entire instance,
    including the endpoint that mints API keys.
    """
    said = str(text or "")
    for secret in secrets:
        text_secret = str(secret or "")
        # Short strings are skipped on purpose: a two-character "key" would
        # turn every occurrence of those letters in an error message into
        # asterisks, and an unreadable error is its own kind of failure.
        if len(text_secret) >= 8:
            said = said.replace(text_secret, "***")
    return said


class N8nClient:
    """One n8n instance."""

    def __init__(
        self,
        url: str,
        api_key: str = "",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Any = None,
    ) -> None:
        self.url = str(url or "").rstrip("/")
        self.api_key = str(api_key or "")
        self.timeout = float(timeout)
        #: Injected by tests. In production httpx opens its own connection.
        self._transport = transport

    # --- plumbing ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers[KEY_HEADER] = self.api_key
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        if not self.url:
            raise N8nError("no n8n URL is configured.")
        target = f"{self.url}{API_PREFIX}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport
            ) as client:
                response = await client.request(
                    method,
                    target,
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                )
        except httpx.TimeoutException:
            raise N8nError(
                f"n8n at {self.url} did not answer within {self.timeout:.0f}s."
            ) from None
        except httpx.HTTPError as err:
            raise N8nError(
                f"could not reach n8n at {self.url}: {redact(err, self.api_key)}"
            ) from None

        if response.status_code == 401:
            raise N8nError(
                "n8n refused the API key (401). Create one in n8n under "
                "Settings -> n8n API and set it in `n8n: api_key:`."
            )
        if response.status_code == 404:
            raise N8nError(
                f"n8n answered 404 for {path}. Either the thing is not there, or "
                "this n8n is too old for the public API used here — the console's "
                "CHECK button says which."
            )
        if response.status_code >= 400:
            raise N8nError(
                f"n8n answered {response.status_code} for {method} {path}: "
                f"{redact(response.text, self.api_key)[:400]}"
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise N8nError(
                f"n8n answered {method} {path} with something that is not JSON. "
                "Is that URL really an n8n instance?"
            ) from None

    # --- workflows --------------------------------------------------------
    async def list_workflows(
        self, *, limit: int = 50, active: bool | None = None, cursor: str = ""
    ) -> tuple[list[dict[str, Any]], str]:
        """A page of workflows and the cursor for the next, or ""."""
        params: dict[str, Any] = {"limit": max(1, min(int(limit), MAX_PAGE))}
        if active is not None:
            # n8n wants the string, and `str(True)` is `True` with a capital T.
            params["active"] = "true" if active else "false"
        if cursor:
            params["cursor"] = cursor
        body = await self._request("GET", "/workflows", params=params)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise N8nError(
                "n8n's workflow list did not look like one. "
                f"Got: {str(body)[:200]}"
            )
        return [w for w in data if isinstance(w, dict)], str(
            (body.get("nextCursor") if isinstance(body, dict) else "") or ""
        )

    async def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        body = await self._request("GET", f"/workflows/{_ident(workflow_id)}")
        if not isinstance(body, dict):
            raise N8nError("n8n did not return a workflow.")
        return body

    async def create_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = await self._request("POST", "/workflows", json_body=payload)
        if not isinstance(body, dict) or not body.get("id"):
            raise N8nError(f"n8n accepted the workflow but returned no id: {str(body)[:200]}")
        return body

    async def update_workflow(
        self, workflow_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        body = await self._request(
            "PUT", f"/workflows/{_ident(workflow_id)}", json_body=payload
        )
        if not isinstance(body, dict):
            raise N8nError("n8n did not return the updated workflow.")
        return body

    async def set_active(self, workflow_id: str, active: bool) -> dict[str, Any]:
        verb = "activate" if active else "deactivate"
        body = await self._request("POST", f"/workflows/{_ident(workflow_id)}/{verb}")
        return body if isinstance(body, dict) else {}

    # --- executions -------------------------------------------------------
    async def executions(
        self, *, workflow_id: str = "", limit: int = 20, status: str = ""
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, min(int(limit), MAX_PAGE))}
        if workflow_id:
            params["workflowId"] = _ident(workflow_id)
        if status:
            params["status"] = status
        body = await self._request("GET", "/executions", params=params)
        data = body.get("data") if isinstance(body, dict) else None
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    # --- the probe --------------------------------------------------------
    async def probe(self) -> dict[str, Any]:
        """"Does this actually work?", answered in a sentence.

        Deliberately the smallest real call rather than a health endpoint: a
        `/healthz` that answers says the process is up, which is not the
        question. The question is whether THIS url, THIS key and THIS API
        version can list a workflow.
        """
        if not self.url:
            return {"ok": False, "detail": "No URL is configured for n8n."}
        if not self.api_key:
            return {
                "ok": False,
                "detail": (
                    "No API key. Create one in n8n under Settings -> n8n API, "
                    "then set `n8n: api_key:` (or the N8N_API_KEY variable)."
                ),
            }
        try:
            workflows, _cursor = await self.list_workflows(limit=1)
        except N8nError as err:
            return {"ok": False, "detail": str(err)}
        return {
            "ok": True,
            "detail": (
                f"Connected to {self.url}. The API answered and the workflow "
                f"list is readable ({len(workflows)} returned by a one-item page)."
            ),
        }


def _ident(value: Any) -> str:
    """A workflow id, and never a path.

    The ids come back from n8n, but they also come in from a model and from a
    request body. `../../` in one of those would walk out of `/api/v1` into
    whatever else the instance serves.
    """
    text = str(value or "").strip()
    if not text:
        raise N8nError("no workflow id was given.")
    if not all(ch.isalnum() or ch in "-_" for ch in text):
        raise N8nError(
            f"{text!r} is not a workflow id. They are letters, digits, dashes "
            "and underscores."
        )
    return text
