"""Builds the FastAPI app: REST routes, the websocket, CORS and the frontend.

    from jarvis.api import create_app
    app = create_app(jarvis)

The app owns no lifecycle — ``python -m jarvis`` starts and stops Jarvis around
it — so tests can wrap a hand-built Jarvis in a ``TestClient`` and get the real
server with nothing faked but the hardware.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..const import VERSION
from .common import HA_VERSION
from .rest import api_router, open_router
from .websocket import websocket_endpoint

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

WEBSOCKET_PATH = "/api/websocket"
#: Where a built web client is dropped: <repo>/www next to the jarvis package.
DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[2] / "www"


def default_cors_origins(jarvis: "Jarvis") -> list[str]:
    options = (jarvis.config or {}).get("jarvis") or {}
    configured = options.get("cors_allowed_origins") if isinstance(options, dict) else None
    if isinstance(configured, str):
        return [configured]
    if isinstance(configured, list) and configured:
        return [str(origin) for origin in configured]
    return ["*"]


def create_app(
    jarvis: "Jarvis",
    *,
    cors_origins: list[str] | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    """The ASGI app every Jarvis client talks to."""
    app = FastAPI(
        title="Jarvis Core",
        version=VERSION,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.jarvis = jarvis
    app.state.ha_version = HA_VERSION

    origins = cors_origins if cors_origins is not None else default_cors_origins(jarvis)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,  # bearer tokens, not cookies
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(open_router)
    app.include_router(api_router)
    app.add_api_websocket_route(WEBSOCKET_PATH, websocket_endpoint, name="websocket")

    static = Path(static_dir) if static_dir is not None else DEFAULT_STATIC_DIR
    if static.is_dir():
        # Mounted last so /api/* and /healthz always win.
        app.mount("/", StaticFiles(directory=str(static), html=True), name="frontend")
        _LOGGER.info("Serving the web client from %s", static)
    else:

        @app.get("/", include_in_schema=False)
        async def index() -> dict[str, Any]:
            return {
                "name": "Jarvis Core",
                "version": VERSION,
                "ha_version": HA_VERSION,
                "websocket": WEBSOCKET_PATH,
                "message": (
                    f"No web client built into {static}. "
                    "The API is up; point a client at this host."
                ),
            }

    return app
