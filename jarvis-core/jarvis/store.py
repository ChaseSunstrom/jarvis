"""Atomic JSON persistence for registries and integration data."""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"version": self.version, "data": data}, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)
