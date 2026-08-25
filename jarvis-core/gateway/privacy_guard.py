"""The privacy guard, inside the proxy.

Jarvis tags a request `local-only` when its prompt carries memory, notes or
private-integration content. This is the half that ENFORCES it — in the proxy,
before a token is generated, so that anything talking to this endpoint is bound
by it and not only the client that happens to be well behaved.

    general_settings:
      custom_auth: privacy_guard.privacy_auth

## Why this is the auth hook and not a callback or a guardrail

Two earlier versions of this file did nothing, and both looked right:

* `litellm_settings: callbacks:` registers something that WATCHES a request.
  The proxy dispatches `async_pre_call_hook` to a callback only under
  conditions this did not meet; it loaded cleanly, logged nothing, and let a
  tagged request through to the cloud.
* `guardrails:` is the mechanism meant for refusing — and custom guardrails
  route through `initialize_callbacks_on_proxy(premium_user=…)`, so on the
  free image the block is accepted and the guardrail never runs.

`custom_auth` runs on **every** request, receives the whole `Request`, and may
raise. It is not a licensed feature, and a request that cannot be authenticated
is a request that never reaches a provider — which is exactly the property
needed here. `testing/fixtures/gateway_probe.py` is what caught both failures:
it asserts the cloud mock heard NOTHING, rather than trusting a log line.

Taking over authentication means implementing it: the master key is checked
here, because a hook that replaces the check must do the check.

## The tag

Arrives two ways, and either is enough:

    header    x-jarvis-privacy: local-only
    body      {"metadata": {"privacy": "local-only"}}

A refusal is a 403 the caller sees. Not a silent downgrade to a local model —
that would be a decision nobody made, and a turn that quietly got worse is
indistinguishable from a turn that quietly leaked.
"""

from __future__ import annotations

import logging
import os
from typing import Any

try:  # pragma: no cover - the proxy provides these at runtime
    from fastapi import HTTPException
    from litellm.proxy._types import UserAPIKeyAuth
except Exception:  # pragma: no cover - importable for tests without litellm
    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int = 400, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class UserAPIKeyAuth:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs


log = logging.getLogger("privacy_guard")

LOCAL_ONLY = "local-only"
ALLOW_CLOUD = "allow-cloud"
HEADER = "x-jarvis-privacy"

#: Model ids that leave the house. Kept in step with
#: `jarvis/security/privacy.py::CLOUD_PREFIXES` by a test that reads both.
CLOUD_PREFIXES = (
    "openai/", "anthropic/", "gemini/", "vertex_ai/", "azure/", "bedrock/",
    "openrouter/", "groq/", "mistral/", "cohere/", "deepseek/", "xai/",
    "together_ai/", "fireworks_ai/", "perplexity/",
)

#: Names in `config.yaml` that are local whatever they resolve to. A request
#: names one of these; the provider prefix only appears further in.
LOCAL_NAMES = ("house", "house-fast")

#: Names in the PROBE's config that stand in for a provider. Listed here so the
#: guard can be exercised without a real one — a mock cloud is still cloud.
MOCK_CLOUD_NAMES = ("cloud-mock", "flaky-cloud")


def is_cloud(model: str) -> bool:
    name = str(model or "").strip().lower()
    if name in LOCAL_NAMES:
        return False
    if name in MOCK_CLOUD_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in CLOUD_PREFIXES)


def tag_of(body: dict[str, Any], headers: Any = None) -> str:
    """The privacy tag on this request, from the body or from the header."""
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    tag = str(metadata.get("privacy") or "").strip().lower()
    if tag:
        return tag
    if headers is not None:
        # Starlette's Headers is case-insensitive; a plain dict is not, and the
        # header arrives however the client spelled it. Both are read.
        value = None
        try:
            value = headers.get(HEADER)
        except Exception:  # noqa: BLE001 - not a mapping at all
            value = None
        if not value:
            try:
                for key, item in dict(headers).items():
                    if str(key).lower() == HEADER:
                        value = item
                        break
            except Exception:  # noqa: BLE001 - unreadable headers are no tag
                value = None
        if value:
            return str(value).strip().lower()
    return ""


def refusal(model: str, tag: str) -> str:
    """"" if this pairing is allowed, else the refusal to show the caller."""
    if tag == LOCAL_ONLY and is_cloud(model):
        return (
            f"refused by the privacy guard: this request is tagged {LOCAL_ONLY} "
            f"because it carries private content, and {model!r} is not a local "
            "model. Route it locally, or opt in per request with "
            f"metadata.privacy={ALLOW_CLOUD!r}."
        )
    return ""


async def privacy_auth(request: Any, api_key: str) -> Any:
    """Authenticate, then refuse anything private that was aimed off-network.

    Runs before routing on every request. Reading the body here is safe:
    Starlette caches it, so the proxy's own read afterwards sees the same bytes.
    """
    master = os.environ.get("LITELLM_MASTER_KEY", "")
    given = str(api_key or "").replace("Bearer ", "").strip()
    if master and given != master:
        raise HTTPException(status_code=401, detail="invalid key")

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a GET, or a body that is not JSON
        body = {}
    if isinstance(body, dict):
        tag = tag_of(body, getattr(request, "headers", None))
        why = refusal(str(body.get("model") or ""), tag)
        if why:
            log.warning("privacy guard refused a request for %r", body.get("model"))
            raise HTTPException(status_code=403, detail=why)

    return UserAPIKeyAuth(api_key=given or "local")
