"""Atomic JSON persistence for registries and integration data."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)


class Store:
    """A small JSON document persisted under <config>/.storage/<key>.json."""

    def __init__(self, config_dir: str | Path, key: str, version: int = 1) -> None:
        self.path = Path(config_dir) / ".storage" / f"{key}.json"
        self.version = version
        self._lock = asyncio.Lock()

    async def load(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _LOGGER.exception("Corrupt store %s; ignoring", self.path)
            return None
        return payload.get("data") if isinstance(payload, dict) else None

    async def save(self, data: dict[str, Any]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, data)

    def _save_sync(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # A temp file of its own per write, never a shared `<name>.tmp`: the
        # lock above serialises saves, but a task cancelled while awaiting the
        # thread (a timer re-armed at the instant it finished, 27 Aug 2026)
        # releases the lock with its thread still writing, and the next save
        # renamed the shared temp file out from under it —
        # FileNotFoundError at the chmod. mkstemp creates it 0600 already.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f"{self.path.stem}.", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"version": self.version, "data": data}, indent=2, default=str)
                )
            # auth.json holds the pairing secret in the clear — it has to be
            # readable back — so a store must not land group/world readable
            # under the usual 022 umask. Chmod the temp file rather than the
            # live path: after the rename there would be an instant in which
            # any local user could open it, and a credential leaked in that
            # instant stays leaked.
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
