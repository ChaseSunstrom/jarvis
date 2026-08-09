"""``python -m jarvis --config /config`` — start the whole house.

Loads configuration.yaml, builds the :class:`~jarvis.core.Jarvis` object, sets
up every configured integration, then serves the REST + websocket API with
uvicorn until SIGINT/SIGTERM, at which point everything is shut down in order.

    python -m jarvis --config ./config
    python -m jarvis --config ./config --create-token phone
    python -m jarvis --config ./config --host 127.0.0.1 --port 8123 -v
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from .api.server import create_app
from .auth import async_setup_auth
from .config import ConfigError, load_config
from .const import VERSION
from .core import Jarvis

_LOGGER = logging.getLogger("jarvis")

DEFAULT_CONFIG_DIR = "./config"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_LOG_LEVEL = "info"

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

ENV_CONFIG_DIR = "JARVIS_CONFIG"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Jarvis Core — home automation and voice assistant.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=os.environ.get(ENV_CONFIG_DIR, DEFAULT_CONFIG_DIR),
        help="configuration directory (holds configuration.yaml). "
        f"Default: {DEFAULT_CONFIG_DIR} or ${ENV_CONFIG_DIR}",
    )
    parser.add_argument("--host", default=None, help="bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="bind port (default 8080)")
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["debug", "info", "warning", "error", "critical"],
        help="override the log level from configuration.yaml",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="shorthand for --log-level debug"
    )
    parser.add_argument(
        "--create-token",
        metavar="NAME",
        default=None,
        help="mint a long-lived access token, print it, and exit",
    )
    parser.add_argument("--version", action="version", version=f"jarvis {VERSION}")
    return parser.parse_args(argv)


# --- logging ----------------------------------------------------------------
def _level(name: Any, default: int = logging.INFO) -> int:
    resolved = logging.getLevelName(str(name).upper()) if name else None
    return resolved if isinstance(resolved, int) else default


def setup_logging(config: dict[str, Any], override: str | None = None) -> None:
    """Root level from ``jarvis: log_level:`` (or HA-style ``logger:``)."""
    options = config.get("jarvis") or {}
    options = options if isinstance(options, dict) else {}
    logger_config = config.get("logger") or {}
    logger_config = logger_config if isinstance(logger_config, dict) else {}

    level = _level(
        override or options.get("log_level") or logger_config.get("default"),
        _level(DEFAULT_LOG_LEVEL),
    )
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt=DATE_FORMAT)
    logging.getLogger().setLevel(level)

    for name, value in (logger_config.get("logs") or {}).items():
        logging.getLogger(str(name)).setLevel(_level(value, level))

    # uvicorn duplicates access logs through its own handlers; keep it quiet
    # unless we are actually debugging.
    if level > logging.DEBUG:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# --- server -----------------------------------------------------------------
def _server_options(config: dict[str, Any], args: argparse.Namespace) -> tuple[str, int]:
    options = config.get("jarvis") or {}
    options = options if isinstance(options, dict) else {}
    http = options.get("http") if isinstance(options.get("http"), dict) else {}
    host = args.host or options.get("host") or http.get("host") or DEFAULT_HOST
    port = args.port or options.get("port") or http.get("port") or DEFAULT_PORT
    return str(host), int(port)


def _install_signal_handlers(stop: Any) -> Any:
    """Route SIGINT/SIGTERM to `stop`. Returns a callable that undoes it.

    These stay installed for the whole run, including while uvicorn has its own
    handlers up: uvicorn re-raises the signal once ``serve()`` returns, and if
    the only handler left standing were the default one the process would die
    on the spot — taking the orderly Jarvis shutdown with it. Ours absorbs that
    re-raise. It must therefore stay harmless and idempotent; uvicorn already
    owns "stop now" (a second Ctrl-C) on its side.
    """
    loop = asyncio.get_running_loop()
    installed: list[Any] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop, sig)
        except (NotImplementedError, RuntimeError, AttributeError):  # pragma: no cover
            continue
        installed.append(sig)

    def _remove() -> None:
        for sig in installed:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(sig)

    return _remove


async def async_run(args: argparse.Namespace) -> int:
    config_dir = Path(args.config).expanduser().resolve()
    try:
        config = load_config(config_dir)
    except ConfigError as err:
        setup_logging({}, args.log_level)
        _LOGGER.error("Configuration error: %s", err)
        return 2

    setup_logging(config, "debug" if args.verbose else args.log_level)
    host, port = _server_options(config, args)

    jarvis = Jarvis(config_dir)
    await async_setup_auth(jarvis)

    if args.create_token:
        _info, secret = await jarvis.data["auth"].create_token(args.create_token)
        print(secret)  # noqa: T201 - printing it is the point of the flag
        return 0

    # Below the --create-token return on purpose: minting a token is a config-
    # directory operation and must not need a working ASGI server installed.
    import uvicorn

    _LOGGER.info("Jarvis %s starting from %s", VERSION, config_dir)

    server: Any = None
    stopping = False

    def _stop(signum: Any = None) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        _LOGGER.info("Received %s — shutting down", getattr(signum, "name", signum))
        if server is not None:
            server.should_exit = True

    remove_handlers = _install_signal_handlers(_stop)
    try:
        await jarvis.async_setup(config)
        if stopping:  # signalled before the server ever came up
            return 0

        server = uvicorn.Server(
            uvicorn.Config(
                create_app(jarvis),
                host=host,
                port=port,
                log_level=logging.getLogger().level,
                access_log=logging.getLogger().level <= logging.DEBUG,
                lifespan="on",
                ws_ping_interval=20.0,
                ws_ping_timeout=30.0,
            )
        )
        await jarvis.async_start()
        _LOGGER.info(
            "API listening on http://%s:%d — websocket at ws://%s:%d/api/websocket",
            host, port, host, port,
        )
        await server.serve()
    finally:
        remove_handlers()
        await jarvis.async_stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(async_run(args))
    except KeyboardInterrupt:  # pragma: no cover - Ctrl-C outside the handler
        return 130


if __name__ == "__main__":
    sys.exit(main())
