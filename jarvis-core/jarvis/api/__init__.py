"""The Jarvis API server: FastAPI REST routes plus the websocket clients speak.

    from jarvis.api import create_app
"""

from __future__ import annotations

from .common import HA_VERSION, ApiError
from .server import WEBSOCKET_PATH, create_app
from .websocket import WebSocketHandler, websocket_endpoint

__all__ = [
    "ApiError",
    "HA_VERSION",
    "WEBSOCKET_PATH",
    "WebSocketHandler",
    "create_app",
    "websocket_endpoint",
]
