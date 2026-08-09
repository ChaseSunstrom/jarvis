"""Long-lived access tokens — the only credential Jarvis has.

There are no user accounts and no login form. A client (the web HUD, the
Android app, an ESP32 satellite, curl) presents a long-lived token as
``Authorization: Bearer <token>`` on REST calls, or inside the websocket
``auth`` message, and that is the whole authentication story.

Tokens are persisted through :class:`jarvis.store.Store` at
``<config>/.storage/auth.json``. Only a SHA-256 digest is stored: a token is
shown exactly once, when it is created. On first run — no stored tokens and no
``JARVIS_TOKEN`` in the environment — one is minted and printed to the log in a
banner you cannot miss.

    JARVIS_TOKEN=...   overrides/augments the store; always accepted.

All comparisons go through :func:`hmac.compare_digest`, and verification walks
every stored token rather than short-circuiting on the first match, so a
timing observer learns nothing about which token (if any) matched.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .store import Store

if TYPE_CHECKING:  # pragma: no cover
    from .core import Jarvis

_LOGGER = logging.getLogger(__name__)

DATA_AUTH = "auth"
STORAGE_KEY = "auth"
ENV_TOKEN = "JARVIS_TOKEN"

TOKEN_BYTES = 32
DEFAULT_TOKEN_NAME = "initial"
ENV_TOKEN_ID = "env"

__all__ = [
    "AuthManager",
    "DATA_AUTH",
    "ENV_TOKEN",
    "TokenInfo",
    "async_setup_auth",
    "extract_bearer_token",
    "get_auth",
]


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    """A fresh URL-safe secret (~43 chars of base64url, 256 bits)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


@dataclass(slots=True)
class TokenInfo:
    """Metadata about one token. Never carries the secret itself."""

    id: str
    name: str
    token_hash: str = ""
    created_at: float = field(default_factory=time.time)
    last_used_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Safe to hand to a client: the digest stays server-side."""
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }

    def _stored(self) -> dict[str, Any]:
        return {**self.as_dict(), "token_hash": self.token_hash}


class AuthManager:
    """Creates, verifies, lists and revokes long-lived tokens."""

    def __init__(self, store: Store | None = None) -> None:
        self._store = store
        self._tokens: dict[str, TokenInfo] = {}
        self._env_token: str | None = None
        self._loaded = False
        self.refresh_env()

    # --- environment ------------------------------------------------------
    def refresh_env(self) -> str | None:
        """Re-read ``JARVIS_TOKEN`` (tests monkeypatch it after construction)."""
        raw = os.environ.get(ENV_TOKEN)
        self._env_token = raw.strip() or None if raw else None
        return self._env_token

    @property
    def has_env_token(self) -> bool:
        return self.refresh_env() is not None

    # --- persistence ------------------------------------------------------
    async def async_load(self) -> "AuthManager":
        data = await self._store.load() if self._store is not None else None
        if isinstance(data, dict):
            for raw in data.get("tokens") or []:
                if not isinstance(raw, dict) or not raw.get("token_hash"):
                    continue
                info = TokenInfo(
                    id=str(raw.get("id") or uuid.uuid4().hex[:12]),
                    name=str(raw.get("name") or DEFAULT_TOKEN_NAME),
                    token_hash=str(raw["token_hash"]),
                    created_at=float(raw.get("created_at") or time.time()),
                    last_used_at=raw.get("last_used_at"),
                )
                self._tokens[info.id] = info
        self._loaded = True
        return self

    async def async_save(self) -> None:
        if self._store is None:
            return
        try:
            await self._store.save(
                {"tokens": [info._stored() for info in self._tokens.values()]}
            )
        except OSError:  # pragma: no cover - disk trouble must not kill the API
            _LOGGER.warning("Could not persist auth tokens", exc_info=True)

    # --- tokens -----------------------------------------------------------
    async def create_token(self, name: str = DEFAULT_TOKEN_NAME) -> tuple[TokenInfo, str]:
        """Mint a token. Returns ``(info, secret)``; the secret is not stored."""
        secret = generate_token()
        info = TokenInfo(
            id=uuid.uuid4().hex[:12],
            name=str(name or DEFAULT_TOKEN_NAME),
            token_hash=_digest(secret),
        )
        self._tokens[info.id] = info
        await self.async_save()
        return info, secret

    def verify(self, token: str | None) -> TokenInfo | None:
        """The token's :class:`TokenInfo`, or ``None`` when it is not valid."""
        if not token:
            return None
        candidate = _digest(token)

        env = self.refresh_env()
        if env is not None and hmac.compare_digest(_digest(env), candidate):
            return TokenInfo(ENV_TOKEN_ID, ENV_TOKEN, last_used_at=time.time())

        matched: TokenInfo | None = None
        for info in self._tokens.values():
            # No early exit: every stored token is compared, every time.
            if hmac.compare_digest(info.token_hash, candidate):
                matched = info
        if matched is not None:
            matched.last_used_at = time.time()
        return matched

    def is_valid(self, token: str | None) -> bool:
        return self.verify(token) is not None

    def list_tokens(self) -> list[TokenInfo]:
        """Stored tokens (the environment token is not listed or revocable)."""
        return list(self._tokens.values())

    async def revoke(self, token_id: str) -> bool:
        if self._tokens.pop(token_id, None) is None:
            return False
        await self.async_save()
        return True

    # --- first run --------------------------------------------------------
    async def async_ensure_initial_token(self, name: str = DEFAULT_TOKEN_NAME) -> str | None:
        """Mint + log the first token when there is no other way in."""
        if self._tokens or self.has_env_token:
            return None
        _info, secret = await self.create_token(name)
        rule = "=" * 74
        _LOGGER.warning(
            "\n%s\n"
            "  JARVIS CREATED YOUR FIRST LONG-LIVED ACCESS TOKEN (%r).\n"
            "  Copy it into the web HUD / Android app now — it is never shown again:\n"
            "\n"
            "      %s\n"
            "\n"
            "  Lost it? Delete %s and restart to mint a new one.\n"
            "%s",
            rule,
            name,
            secret,
            self._store.path if self._store is not None else "<config>/.storage/auth.json",
            rule,
        )
        return secret


# `list` reads better from the outside; `list_tokens` avoids shadowing the
# builtin inside the class body.
AuthManager.list = AuthManager.list_tokens  # type: ignore[attr-defined]


def extract_bearer_token(header: str | None) -> str | None:
    """Pull the token out of an ``Authorization: Bearer <token>`` header."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def get_auth(jarvis: "Jarvis") -> AuthManager | None:
    manager = jarvis.data.get(DATA_AUTH)
    return manager if isinstance(manager, AuthManager) else None


async def async_setup_auth(
    jarvis: "Jarvis",
    store: Store | None = None,
    config_dir: str | Path | None = None,
) -> AuthManager:
    """Load (or create) the token store and hang it off ``jarvis.data``."""
    existing = get_auth(jarvis)
    if existing is not None:
        return existing
    if store is None:
        store = Store(config_dir or jarvis.config_dir, STORAGE_KEY)
    manager = AuthManager(store)
    await manager.async_load()
    await manager.async_ensure_initial_token()
    jarvis.data[DATA_AUTH] = manager
    return manager
