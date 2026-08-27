"""The console's door to the environment (M114): list, set, clear, reveal — and restart."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import TYPE_CHECKING, Any

from ..environment import Environment

if TYPE_CHECKING:
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)
DATA_KEY = "environment"


def get_environment(jarvis: "Jarvis") -> Environment:
    env = jarvis.data.get(DATA_KEY)
    if not isinstance(env, Environment):
        env = jarvis.data[DATA_KEY] = Environment.load(jarvis.config_dir)
    return env


def environment_list_payload(jarvis: "Jarvis") -> dict[str, Any]:
    env = get_environment(jarvis)
    rows = env.rows()
    return {
        "variables": rows,
        "count": len(rows),
        "pending": sum(1 for r in rows if r["pending"]),
        "store": str(env.config_dir / ".storage" / "environment.json"),
        "note": (
            "What is set here is kept and applied over the container's environment at "
            "the next restart, before configuration is read. The file on the host is "
            "never written."
        ),
    }


async def async_environment_set(jarvis: "Jarvis", msg: dict[str, Any]) -> dict[str, Any]:
    env = get_environment(jarvis)
    result = env.set(str(msg.get("name") or ""), msg.get("value"))
    if result.get("status") == "ok":
        _fire(jarvis, result["name"], "set")
    return result


async def async_environment_clear(jarvis: "Jarvis", msg: dict[str, Any]) -> dict[str, Any]:
    env = get_environment(jarvis)
    result = env.clear(str(msg.get("name") or ""))
    if result.get("status") == "ok":
        _fire(jarvis, result["name"], "cleared")
    return result


async def async_environment_reveal(jarvis: "Jarvis", msg: dict[str, Any]) -> dict[str, Any]:
    return get_environment(jarvis).reveal(str(msg.get("name") or ""))


def _fire(jarvis: "Jarvis", name: str, action: str) -> None:
    try:
        jarvis.bus.fire("jarvis_environment_changed", {"name": name, "action": action})
    except Exception:  # pragma: no cover - the change is kept either way
        _LOGGER.debug("Could not announce the environment change", exc_info=True)


async def async_restart(jarvis: "Jarvis", msg: dict[str, Any]) -> dict[str, Any]:
    """Stop the process cleanly; the container's restart policy brings it back.

    The one way a console-set variable takes effect. Said out loud in the
    log, and the reply goes out BEFORE the stop so the console hears "yes"
    and then loses the socket, in that order.
    """
    delay = max(0.2, min(5.0, float(msg.get("delay") or 0.5)))
    _LOGGER.warning("Restart asked for from the console; stopping in %.1f s", delay)

    def _stop() -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.get_running_loop().call_later(delay, _stop)
    return {"status": "ok", "restarting_in": delay}


__all__ = [
    "async_environment_clear",
    "async_environment_reveal",
    "async_environment_set",
    "async_restart",
    "environment_list_payload",
    "get_environment",
]
