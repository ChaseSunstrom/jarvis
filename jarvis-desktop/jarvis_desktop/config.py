"""Configuration: where the agent connects, what it is allowed to touch.

Precedence, lowest to highest: built-in defaults, config file, environment
variables, command-line flags. Everything the security model depends on lives
here and nothing in it can be set by the server — a ``device_command`` cannot
add a file root, widen the shell denylist, or turn on ``shell=True``.

The config file is plain JSON so it can be read, diffed and version-controlled::

    {
      "server_url": "ws://jarvis.lan:8080/api/websocket",
      "device_name": "workshop-desktop",
      "file_roots": ["~/jarvis-workspace"],
      "shell": {"enabled": true, "use_shell": false, "timeout_s": 30},
      "input_automation": {"enabled": false}
    }
"""

from __future__ import annotations

import json
import os
import platform
import socket
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

__all__ = ["Config", "ShellConfig", "InputConfig", "default_config_path", "load_config"]


def _expand(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path))))


def default_state_dir() -> Path:
    """Where policy, audit and device identity live.

    Follows XDG on Linux, ``~/Library/Application Support`` on macOS and
    ``%APPDATA%`` on Windows, and never falls back to a world-readable temp dir.
    """
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "jarvis-desktop"
    if system == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "jarvis-desktop"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "jarvis-desktop"


def default_config_path() -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "jarvis-desktop" / "config.json"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "jarvis-desktop" / "config.json"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "jarvis-desktop" / "config.json"


@dataclass(frozen=True)
class ShellConfig:
    """``run_command`` settings. Tier 3 regardless of what is set here."""

    #: When false the action reports itself unsupported and never runs anything.
    enabled: bool = True
    #: Opt-in to ``shell=True``. Off by default: without it the command is split
    #: with ``shlex`` and exec'd directly, so ``;``, ``&&``, backticks and
    #: redirection are literal argv text rather than shell syntax.
    use_shell: bool = False
    timeout_s: float = 30.0
    #: Bytes of stdout/stderr kept. The rest is dropped and flagged truncated.
    max_output_bytes: int = 64 * 1024
    #: Extra regexes refused outright, on top of the built-in denylist.
    extra_denylist: tuple[str, ...] = ()
    #: Environment variables passed through to the child, in addition to the
    #: built-in safe set. Anything matching a secret-ish name is dropped anyway.
    env_passthrough: tuple[str, ...] = ()
    #: Working directory for commands. None => the first file root.
    cwd: str | None = None


@dataclass(frozen=True)
class InputConfig:
    """Synthetic keyboard/mouse. Off by default — it can drive any app on the
    machine, so it is opt-in even before the Tier-3 prompt."""

    enabled: bool = False
    #: Screenshots land here (inside a file root) rather than being returned
    #: inline; the model gets a path, not a megabyte of base64.
    screenshot_dir: str | None = None


@dataclass(frozen=True)
class Config:
    # --- connection ---------------------------------------------------------
    server_url: str = "ws://127.0.0.1:8080/api/websocket"
    token: str = ""
    #: Host pinning: once set, the agent refuses to connect anywhere else, so a
    #: rewritten config or a redirect cannot move it to another server.
    pinned_host: str | None = None
    #: Refuse plaintext ws:// outside the LAN/loopback/WireGuard.
    allow_plaintext_ws: bool = True

    # --- identity -----------------------------------------------------------
    device_id: str = ""
    device_name: str = ""
    app_version: str = "0.1.0"

    # --- storage ------------------------------------------------------------
    state_dir: Path = field(default_factory=default_state_dir)
    #: Directories every file action is confined to. The first one is the
    #: default workspace: relative paths resolve against it.
    file_roots: tuple[Path, ...] = ()

    # --- capability switches ------------------------------------------------
    shell: ShellConfig = field(default_factory=ShellConfig)
    input_automation: InputConfig = field(default_factory=InputConfig)
    clipboard_enabled: bool = True
    notifications_enabled: bool = True

    # --- channel behaviour --------------------------------------------------
    command_rate_capacity: float = 10.0
    command_rate_per_second: float = 1.0
    event_rate_capacity: float = 20.0
    event_rate_per_second: float = 2.0
    max_concurrent_commands: int = 4
    consent_timeout_s: float = 60.0
    #: When true the consent prompt is skipped and everything that needs one is
    #: denied. For a headless service where no human can answer.
    headless_deny: bool = False

    # --- triggers -----------------------------------------------------------
    triggers: tuple[dict[str, Any], ...] = ()

    # --- derived ------------------------------------------------------------
    @property
    def policy_path(self) -> Path:
        return self.state_dir / "policy.json"

    @property
    def audit_path(self) -> Path:
        return self.state_dir / "audit.jsonl"

    @property
    def identity_path(self) -> Path:
        return self.state_dir / "device.json"

    @property
    def workspace(self) -> Path:
        return self.file_roots[0] if self.file_roots else (self.state_dir / "workspace")

    @property
    def server_host(self) -> str:
        """Hostname of the configured jarvis-core server.

        This is the single exemption in the SSRF guard: it is the machine we
        already talk to over an authenticated socket, so it stays reachable even
        though it usually lives on the LAN.
        """
        try:
            return (urlparse(self.server_url).hostname or "").lower()
        except ValueError:
            return ""

    @property
    def server_origin(self) -> str:
        parsed = urlparse(self.server_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def ensure_dirs(self) -> "Config":
        """Create the state dir and every file root. Called once at startup."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.state_dir, 0o700)
        except OSError:
            pass
        for root in self.file_roots or (self.workspace,):
            root.mkdir(parents=True, exist_ok=True)
        return self

    def with_identity(self) -> "Config":
        """Fill in a stable device id/name, persisting the id on first run."""
        device_id = self.device_id
        name = self.device_name or socket.gethostname() or "desktop"
        if not device_id:
            device_id = _load_or_create_device_id(self.identity_path)
        return replace(self, device_id=device_id, device_name=name)

    def capabilities(self) -> list[str]:
        """Coarse buckets advertised in ``jarvis/device/register``.

        Derived from the switches above, not from the action table, so a
        capability the user turned off is never advertised.
        """
        caps = ["system", "apps", "files", "http"]
        if self.clipboard_enabled:
            caps.append("clipboard")
        if self.notifications_enabled:
            caps.append("notify")
        if self.shell.enabled:
            caps.append("shell")
        if self.input_automation.enabled:
            caps.append("ui_automation")
        return sorted(caps)


def _load_or_create_device_id(path: Path) -> str:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        stored = raw.get("device_id")
        if isinstance(stored, str) and stored.strip():
            return stored.strip()
    except (OSError, ValueError, AttributeError):
        pass
    device_id = f"desktop-{uuid.uuid4().hex[:12]}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"device_id": device_id}, indent=2), encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        pass
    return device_id


# --- loading ----------------------------------------------------------------


def _as_bool(value: object, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
    return fallback


def normalize_server_url(raw: str) -> str:
    """Accept ``host``, ``host:8080``, ``http://host`` or a full ws URL.

    Always ends up as a ``ws://``/``wss://`` URL pointing at ``/api/websocket``,
    because that is the only endpoint this agent speaks.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "ws://" + text
    parsed = urlparse(text)
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    if scheme not in ("ws", "wss"):
        raise ValueError(f"server url must be ws:// or wss:// (got {parsed.scheme}://)")
    path = parsed.path or ""
    if path in ("", "/"):
        path = "/api/websocket"
    return urlunparse((scheme, parsed.netloc, path, "", parsed.query, ""))


def load_config(
    config_path: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Config:
    """Build a :class:`Config` from file, then environment, then overrides."""
    env = os.environ if env is None else env
    raw: dict[str, Any] = {}

    path = Path(config_path) if config_path else default_config_path()
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, ValueError) as exc:
            raise ValueError(f"could not read config {path}: {exc}") from exc

    def pick(key: str, env_key: str, default: Any = None) -> Any:
        if overrides and overrides.get(key) not in (None, ""):
            return overrides[key]
        if env.get(env_key):
            return env[env_key]
        return raw.get(key, default)

    state_dir = _expand(pick("state_dir", "JARVIS_STATE_DIR", default_state_dir()))

    roots_raw = pick("file_roots", "JARVIS_FILE_ROOTS", None)
    if isinstance(roots_raw, str):
        roots_raw = [p for p in roots_raw.split(os.pathsep) if p.strip()]
    if not roots_raw:
        roots_raw = [str(Path.home() / "jarvis-workspace")]
    file_roots = tuple(_expand(p).resolve() for p in roots_raw)

    shell_raw = raw.get("shell", {}) if isinstance(raw.get("shell"), dict) else {}
    shell = ShellConfig(
        enabled=_as_bool(env.get("JARVIS_SHELL_ENABLED", shell_raw.get("enabled", True)), True),
        use_shell=_as_bool(
            env.get("JARVIS_SHELL_USE_SHELL", shell_raw.get("use_shell", False)), False
        ),
        timeout_s=float(shell_raw.get("timeout_s", 30.0)),
        max_output_bytes=int(shell_raw.get("max_output_bytes", 64 * 1024)),
        extra_denylist=tuple(shell_raw.get("extra_denylist", ()) or ()),
        env_passthrough=tuple(shell_raw.get("env_passthrough", ()) or ()),
        cwd=shell_raw.get("cwd"),
    )

    input_raw = (
        raw.get("input_automation", {})
        if isinstance(raw.get("input_automation"), dict)
        else {}
    )
    input_automation = InputConfig(
        enabled=_as_bool(
            env.get("JARVIS_INPUT_ENABLED", input_raw.get("enabled", False)), False
        ),
        screenshot_dir=input_raw.get("screenshot_dir"),
    )

    server_url = normalize_server_url(str(pick("server_url", "JARVIS_SERVER", "") or ""))
    token = str(pick("token", "JARVIS_TOKEN", "") or "")
    if not token and env.get("JARVIS_TOKEN_FILE"):
        try:
            token = Path(_expand(env["JARVIS_TOKEN_FILE"])).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"could not read JARVIS_TOKEN_FILE: {exc}") from exc

    triggers = raw.get("triggers", ())
    if not isinstance(triggers, (list, tuple)):
        triggers = ()

    cfg = Config(
        server_url=server_url or Config.server_url,
        token=token,
        pinned_host=(pick("pinned_host", "JARVIS_PINNED_HOST", None) or None),
        allow_plaintext_ws=_as_bool(raw.get("allow_plaintext_ws", True), True),
        device_id=str(pick("device_id", "JARVIS_DEVICE_ID", "") or ""),
        device_name=str(pick("device_name", "JARVIS_DEVICE_NAME", "") or ""),
        state_dir=state_dir,
        file_roots=file_roots,
        shell=shell,
        input_automation=input_automation,
        clipboard_enabled=_as_bool(raw.get("clipboard_enabled", True), True),
        notifications_enabled=_as_bool(raw.get("notifications_enabled", True), True),
        consent_timeout_s=float(raw.get("consent_timeout_s", 60.0)),
        headless_deny=_as_bool(
            env.get("JARVIS_HEADLESS_DENY", raw.get("headless_deny", False)), False
        ),
        max_concurrent_commands=int(raw.get("max_concurrent_commands", 4)),
        command_rate_capacity=float(raw.get("command_rate_capacity", 10.0)),
        command_rate_per_second=float(raw.get("command_rate_per_second", 1.0)),
        triggers=tuple(t for t in triggers if isinstance(t, dict)),
    )
    return cfg.with_identity()
