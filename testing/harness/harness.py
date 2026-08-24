#!/usr/bin/env python3
"""Boot the REAL jarvis-core against fake model and voice backends.

This is the foundation every end-to-end test in this repo stands on. It:

1. starts a fake Ollama and a fake Wyoming stack (STT/TTS/wake) on free ports,
2. writes a complete, throwaway jarvis-core config pointed at them,
3. starts the real ``python -m jarvis --config <tmp>`` as a subprocess,
4. waits for ``/healthz`` (polling — never a bare sleep),
5. proves the deterministic token works, and
6. prints one JSON line describing everything a client needs.

No GPU, no models, no microphone, no hardware. What is under test is the real
server: the real websocket framing, the real pipeline runner, the real tool
registry, the real device channel. Only the two things that need a GPU are
replaced, and both are replaced at the wire protocol, not by monkey-patching.

    # one-shot, prints the JSON and exits (everything is torn down)
    python3 harness.py

    # keep it up for an emulator or a manual poke, until SIGTERM
    python3 harness.py --wait --json-out /tmp/harness.json

    # from a test
    from testing.harness import Harness
    with Harness() as h:
        h.base_url, h.ws_url, h.token, h.ports

The token is deterministic because ``JARVIS_TOKEN`` is jarvis-core's own
documented override (see ``jarvis/auth.py``): it is always accepted and never
written to disk, so every run of the harness authenticates the same way.

The server binds 0.0.0.0 on purpose. An Android emulator reaches the host at
10.0.2.2, and the printed JSON carries ready-made ``emulator_*`` URLs for it.
Everything else about the box stays untouched: a temp config directory, a temp
recorder database, temp tokens, and no port that a real install uses.
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
CORE_DIR = REPO_ROOT / "jarvis-core"
DEFAULT_OLLAMA_SCRIPT = HERE / "scripts" / "default.json"

DEFAULT_TOKEN = "jarvis-test-token-0000000000000000000000"
DEFAULT_MODEL = "qwen3:8b"

#: Long enough for a cold import of fastapi/uvicorn on a slow CI runner.
BOOT_TIMEOUT = 90.0
#: How long a child gets to print its port file before we call it dead.
CHILD_TIMEOUT = 30.0
#: SIGTERM grace before SIGKILL.
STOP_TIMEOUT = 15.0
#: How many times to re-draw the port when something else got there first.
#: Only ever spent on a bind conflict; every other boot failure is reported.
CORE_BOOT_ATTEMPTS = 4

POLL_INTERVAL = 0.1

__all__ = ["Harness", "HarnessError", "main"]


class HarnessError(RuntimeError):
    """The harness could not bring the stack up."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def free_port(host: str = "0.0.0.0", avoid: Any = ()) -> int:
    """A port nothing is listening on right now, and not one of ``avoid``.

    Racy in principle (something could take it in the microseconds before the
    server binds) and unavoidable: jarvis-core is told its port on the command
    line. The fakes do not need this — they bind 0 and report back.

    ``avoid`` matters because the fakes bind *after* this is first called: the
    kernel is free to hand one of them the port jarvis-core is about to want,
    and the failure then lands on the server's bind with no hint of why. Each
    rejected candidate is held open while the next is drawn, so the kernel has
    to offer a different one rather than the same one again.
    """
    unwanted = {int(port) for port in avoid}
    held: list[socket.socket] = []
    try:
        for _ in range(32):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, 0))
            port = int(sock.getsockname()[1])
            if port not in unwanted:
                sock.close()
                return port
            held.append(sock)
    finally:
        for sock in held:
            with contextlib.suppress(OSError):
                sock.close()
    raise HarnessError(f"could not find a free port outside {sorted(unwanted)}")


def port_is_free(port: int, host: str = "0.0.0.0") -> bool:
    """Can something bind ``port`` right now?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


def http_json(
    url: str,
    token: str | None = None,
    timeout: float = 5.0,
    payload: Any = None,
    method: str | None = None,
) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else None


def wait_for(check, timeout: float, interval: float = POLL_INTERVAL, on_dead=None):
    """Poll ``check()`` until it returns something truthy. Never a bare sleep.

    ``on_dead`` is called each round; if it returns a string, that is the
    reason the wait is abandoned early (a child process died) — waiting the
    full timeout for something that has already exited helps nobody.
    """
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if on_dead is not None:
            dead = on_dead()
            if dead:
                raise HarnessError(dead)
        try:
            result = check()
        except Exception as err:  # noqa: BLE001 - the whole point is to retry
            last_error = err
            result = None
        if result:
            return result
        time.sleep(interval)
    suffix = f" (last error: {last_error})" if last_error else ""
    raise HarnessError(f"timed out after {timeout:g}s{suffix}")


def tail(path: Path, lines: int = 40) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"(no log at {path})"
    return "\n".join(content[-lines:])


# ---------------------------------------------------------------------------
# child processes
# ---------------------------------------------------------------------------
class _Child:
    """One subprocess, in its own process group so nothing is ever orphaned."""

    def __init__(self, name: str, argv: list[str], log: Path, cwd: Path | None = None,
                 env: dict[str, str] | None = None) -> None:
        self.name = name
        self.argv = argv
        self.log = log
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.log.open("wb")
        environment = dict(os.environ)
        environment["PYTHONUNBUFFERED"] = "1"
        if env:
            environment.update(env)
        # start_new_session makes this process a group leader, so stop() can
        # signal the whole tree — a uvicorn reloader or a helper thread that
        # spawned something cannot survive us.
        self.process = subprocess.Popen(  # noqa: S603
            argv,
            cwd=str(cwd) if cwd else None,
            env=environment,
            stdout=self._handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    @property
    def pid(self) -> int:
        return self.process.pid

    def alive(self) -> bool:
        return self.process.poll() is None

    def dead_reason(self) -> str | None:
        code = self.process.poll()
        if code is None:
            return None
        return (
            f"{self.name} exited with status {code}. Last of {self.log}:\n"
            f"{tail(self.log)}"
        )

    def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        if self.process.poll() is None:
            self._signal(signal.SIGTERM)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and self.process.poll() is None:
                time.sleep(0.05)
            if self.process.poll() is None:
                self._signal(signal.SIGKILL)
        with contextlib.suppress(Exception):
            self.process.wait(timeout=5)
        with contextlib.suppress(Exception):
            self._handle.close()

    def _signal(self, sig: int) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(self.process.pid), sig)
        with contextlib.suppress(ProcessLookupError, OSError):
            self.process.send_signal(sig)


# ---------------------------------------------------------------------------
# the configuration under test
# ---------------------------------------------------------------------------
def build_config(
    port: int,
    host: str,
    ollama_url: str,
    stt_port: int,
    tts_port: int,
    wake_port: int,
    model: str = DEFAULT_MODEL,
    wyoming_host: str = "127.0.0.1",
) -> str:
    """A complete jarvis-core configuration.yaml, pointed at the fakes.

    Everything a client might exercise is switched on — demo entities so there
    is a real house to change, the voice pipeline, the LLM agent with a
    deterministic persona, the companion/device channel, sensors, automation,
    and the recorder in the throwaway config directory.
    """
    return f"""\
# Written by testing/harness/harness.py. Throwaway: delete the directory.
jarvis:
  name: Jarvis Test Harness
  latitude: 51.5072
  longitude: -0.1276
  elevation: 11
  radius: 100
  time_zone: UTC
  unit_system: metric
  currency: GBP
  country: GB
  log_level: info
  http:
    # 0.0.0.0 on purpose: an Android emulator reaches this host at 10.0.2.2.
    host: {host}
    port: {port}
  cors_allowed_origins:
    - "*"
  webhook_require_auth: false
  areas:
    - name: Living Room
      aliases: [lounge, front room]
    - name: Kitchen
    - name: Bedroom
    - name: Study
      aliases: [office, lab]

recorder:
  db_file: harness.db
  purge_keep_days: 1
  commit_interval: 1
  auto_purge: false

history:
  days: 1

logbook:
  max_entries: 500
  log_service_calls: true

sun:
  update_interval: 600

demo:
  create_areas: true

# One skill, copied in beside this file by the harness. The live rig asks it
# something only the skill knows, which is the only way to tell "the skill was
# loaded" from "the persona happened to say something similar".
skills:
  path: skills

voice:
  language: en
  stt:
    host: {wyoming_host}
    port: {stt_port}
    timeout: 30
  tts:
    host: {wyoming_host}
    port: {tts_port}
    voice: en_GB-alan-medium
    timeout: 30
  wake:
    host: {wyoming_host}
    port: {wake_port}
    model: hey_jarvis
    timeout: 30
  pipelines:
    - name: Jarvis
      voice: en_GB-alan-medium
      wake_word: hey_jarvis
      language: en
    - name: Guest
      voice: en_US-lessac-medium
      wake_word: ok_nabu
      language: en

llm:
  url: {ollama_url}
  model: {model}
  # An inline persona: no prompts/ directory needed, and the system prompt is
  # the same on every run, so the fake model's rules match deterministically.
  persona: "You are Jarvis, a composed British butler. Answer in one sentence."
  max_tool_rounds: 3
  approval_ttl: 60
  options:
    temperature: 0
  expose:
    domains:
      - light
      - switch
      - cover
      - climate
      - fan
      - media_player
      - scene
      - script
      - lock
    entities:
      - sensor.outside_temperature
      - sensor.outside_humidity
      - binary_sensor.front_door
  conversation:
    ttl: 900
    max_turns: 20

# The cross-device channel the phone and the desktop agent speak.
companion:
device_control:
  timeout: 30

sensors:
  allow_auto_register: true
  token: harness-sensor-token

input_boolean:
  harness_flag:
    name: Harness flag
    initial: "off"

input_number:
  harness_level:
    name: Harness level
    min: 0
    max: 100
    step: 1
    initial: 42

input_select:
  house_mode:
    name: House mode
    options: [home, away, night]
    initial: home

input_text:
  harness_note:
    name: Harness note
    max: 200

template:
  - binary_sensor:
      - name: Harness Ready
        state: "{{{{ true }}}}"

automation:
  - alias: Harness flag announces itself
    trigger:
      - platform: state
        entity_id: input_boolean.harness_flag
        to: "on"
    action:
      - service: input_text.set_value
        data:
          entity_id: input_text.harness_note
          value: "flag raised"

# A script and a scene, so a client has one of each to exercise. The script
# has a description, which is what makes it a tool the model can call.
script:
  harness_reset:
    alias: Harness reset
    description: Put the demo house back the way the harness starts it.
    mode: single
    sequence:
      - service: light.turn_off
        target:
          entity_id: light.bed_light
      - service: input_select.select_option
        data:
          entity_id: input_select.house_mode
          option: home

scene:
  - name: Harness Movie
    id: harness_movie
    entities:
      light.ceiling_lights:
        state: on
        brightness: 25
      light.kitchen_lights: off
      switch.decorative_lights: off
"""


# ---------------------------------------------------------------------------
# the harness
# ---------------------------------------------------------------------------
class Harness:
    """The whole stack: two fakes plus the real jarvis-core.

    Use it as a context manager, or call :meth:`start` / :meth:`stop`. Either
    way every child is killed by process group on the way out, including when
    the test process dies unexpectedly (there is an ``atexit`` hook).
    """

    def __init__(
        self,
        work_dir: str | Path | None = None,
        port: int | None = None,
        host: str = "0.0.0.0",
        token: str = DEFAULT_TOKEN,
        model: str = DEFAULT_MODEL,
        ollama_script: str | Path | None = None,
        wyoming_script: str | Path | None = None,
        transcript: str | None = None,
        stt_mode: str | None = None,
        python: str | None = None,
        core_dir: str | Path = CORE_DIR,
        fake_host: str = "127.0.0.1",
        keep: bool = False,
        verbose: bool = False,
        boot_timeout: float = BOOT_TIMEOUT,
        save_audio: bool = True,
        ollama_url: str | None = None,
        wyoming: dict[str, Any] | None = None,
    ) -> None:
        self.host = host
        # The fakes never leave this box: jarvis-core reaches them over
        # loopback and nothing else ever does (an emulator talks to the server,
        # not to them). Binding them on 0.0.0.0 would put the fake Ollama's
        # `/_control` plane — which can rewrite what the model says — on every
        # interface of a shared CI runner for nothing.
        self.fake_host = fake_host
        self.token = token
        self.model = model
        self.python = python or sys.executable
        self.core_dir = Path(core_dir).resolve()
        self.keep = keep
        self.verbose = verbose
        self.boot_timeout = boot_timeout
        self.save_audio = save_audio
        self.transcript = transcript
        self.stt_mode = stt_mode
        #: Point the server at a model server and voice services that are
        #: ALREADY RUNNING instead of at the fakes. This is what the live
        #: interaction rig uses: the whole point of that rig is that the STT is
        #: really Whisper, the TTS is really Piper and the model really thinks,
        #: so a fake in the middle of it would prove nothing about any of them.
        #:
        #: `wyoming` is `{"host": ..., "stt": port, "tts": port, "wake": port}`.
        #: Either may be set without the other — a scripted model against real
        #: voice services is a useful third thing.
        self.external_ollama_url = str(ollama_url).rstrip("/") if ollama_url else ""
        self.external_wyoming = dict(wyoming) if wyoming else {}
        self.ollama_script = Path(ollama_script).resolve() if ollama_script else DEFAULT_OLLAMA_SCRIPT
        self.wyoming_script = Path(wyoming_script).resolve() if wyoming_script else None

        self._temp = work_dir is None
        self.work_dir = Path(
            work_dir if work_dir is not None else tempfile.mkdtemp(prefix="jarvis-harness-")
        ).resolve()
        self.config_dir = self.work_dir / "config"
        self.log_dir = self.work_dir / "logs"
        self.audio_dir = self.work_dir / "audio"
        for directory in (self.config_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)

        #: An explicit port is honoured as given; an automatic one is only a
        #: first guess, re-drawn in start() once the fakes have really bound.
        self._port_was_chosen = port is not None
        self.port = int(port) if port else free_port(host)
        self.ports: dict[str, int] = {"core": self.port}
        self.ollama_url = ""
        self._children: list[_Child] = []
        self._started = False
        self._atexit_registered = False

        #: Kept so `set_ollama_script(None)` can put the default brain back.
        self.default_ollama_script = self._load_json(self.ollama_script)
        #: The voice fakes are steered through a file they re-read on change,
        #: so the harness owns a copy rather than the caller's original.
        self._wyoming_script_path = self.work_dir / "wyoming-script.json"
        self._wyoming_script_data: dict[str, Any] = self._load_json(self.wyoming_script) or {}
        if transcript is not None:
            self._wyoming_script_data.setdefault("stt", {}).update(
                {"mode": "script", "transcript": transcript}
            )
        if stt_mode:
            self._wyoming_script_data.setdefault("stt", {})["mode"] = stt_mode
        self._write_wyoming_script()

    def _write_wyoming_script(self) -> None:
        """Publish the voice script atomically.

        ``write_text`` truncates first, so a fake reading at the wrong instant
        sees an empty or half-written file. Writing beside it and renaming over
        the top means a reader only ever sees a whole script — and gives the
        file a new inode every time, which is what makes the fakes' change
        detection immune to a filesystem with coarse timestamps.
        """
        temp = self._wyoming_script_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self._wyoming_script_data), encoding="utf-8")
        os.replace(temp, self._wyoming_script_path)

    @staticmethod
    def _load_json(path: Path | None) -> Any:
        if path is None:
            return None
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise HarnessError(f"cannot read script {path}: {err}") from err

    # --- addresses --------------------------------------------------------
    @property
    def client_host(self) -> str:
        return "127.0.0.1" if self.host in ("", "0.0.0.0", "::") else self.host

    @property
    def base_url(self) -> str:
        return f"http://{self.client_host}:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.client_host}:{self.port}/api/websocket"

    @property
    def emulator_base_url(self) -> str:
        """What an Android emulator must use to reach this host."""
        return f"http://10.0.2.2:{self.port}"

    @property
    def emulator_ws_url(self) -> str:
        return f"ws://10.0.2.2:{self.port}/api/websocket"

    def info(self) -> dict[str, Any]:
        """Everything a test runner or a device needs, as plain JSON."""
        return {
            "base_url": self.base_url,
            "ws_url": self.ws_url,
            "emulator_base_url": self.emulator_base_url,
            "emulator_ws_url": self.emulator_ws_url,
            "host": self.host,
            "fake_host": self.fake_host,
            "token": self.token,
            "model": self.model,
            "ports": dict(self.ports),
            "ollama_url": self.ollama_url,
            "ollama_control_url": f"{self.ollama_url}/_control" if self.ollama_url else "",
            "wyoming_script": str(self._wyoming_script_path),
            "work_dir": str(self.work_dir),
            "config_dir": str(self.config_dir),
            "audio_dir": str(self.audio_dir),
            "logs": {child.name: str(child.log) for child in self._children},
            "pids": {child.name: child.pid for child in self._children},
        }

    # --- lifecycle --------------------------------------------------------
    def __enter__(self) -> "Harness":
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    def start(self) -> "Harness":
        if self._started:
            return self
        if not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True
        try:
            self._start_fakes()
            self._settle_core_port()
            self._write_config()
            self._start_core()
            self._wait_healthy()
            self._check_token()
        except BaseException:
            self.stop(cleanup=False)  # keep the logs: something is wrong
            raise
        self._started = True
        return self

    def stop(self, cleanup: bool = True) -> None:
        for child in reversed(self._children):
            child.stop()
        self._children = []
        self._started = False
        if cleanup and self._temp and not self.keep:
            import shutil

            shutil.rmtree(self.work_dir, ignore_errors=True)

    # --- the fakes --------------------------------------------------------
    def _spawn(self, name: str, argv: list[str], env: dict[str, str] | None = None,
               cwd: Path | None = None) -> _Child:
        child = _Child(name, argv, self.log_dir / f"{name}.log", cwd=cwd, env=env)
        self._children.append(child)
        return child

    def _start_fakes(self) -> None:
        self._start_fake_ollama()
        self._start_fake_wyoming()

    def _start_fake_ollama(self) -> None:
        if self.external_ollama_url:
            self.ollama_url = self.external_ollama_url
            return
        ollama_out = self.work_dir / "fake-ollama.json"
        # A reused --work-dir still holds the *last* run's port files. Reading
        # one of those would point this run's config at a dead port and the
        # failure would land somewhere else entirely, so they go first.
        ollama_out.unlink(missing_ok=True)
        argv = [
            self.python, str(HERE / "fake_ollama.py"),
            "--host", self.fake_host, "--port", "0",
            "--json-out", str(ollama_out),
        ]
        if self.ollama_script:
            argv += ["--script", str(self.ollama_script)]
        if self.verbose:
            argv.append("--verbose")
        ollama = self._spawn("fake-ollama", argv)
        info = self._read_child_json(ollama, ollama_out)
        self.ports["ollama"] = int(info["port"])
        self.ollama_url = f"http://{self.fake_host}:{self.ports['ollama']}"

    def _start_fake_wyoming(self) -> None:
        if self.external_wyoming:
            self.wyoming_host = str(self.external_wyoming.get("host") or "127.0.0.1")
            for name, default in (("stt", 10300), ("tts", 10200), ("wake", 10400)):
                self.ports[name] = int(self.external_wyoming.get(name) or default)
            return
        wyoming_out = self.work_dir / "fake-wyoming.json"
        wyoming_out.unlink(missing_ok=True)
        argv = [
            self.python, str(HERE / "fake_wyoming.py"),
            "--host", self.fake_host,
            "--stt-port", "0", "--tts-port", "0", "--wake-port", "0",
            # Only ever --script: CLI overrides would outrank the file, and
            # then set_transcript() would silently do nothing.
            "--script", str(self._wyoming_script_path),
            "--json-out", str(wyoming_out),
        ]
        if self.save_audio:
            argv += ["--audio-dir", str(self.audio_dir)]
        if self.verbose:
            argv.append("--verbose")
        wyoming = self._spawn("fake-wyoming", argv)
        info = self._read_child_json(wyoming, wyoming_out)
        self.ports["stt"] = int(info["stt_port"])
        self.ports["tts"] = int(info["tts_port"])
        self.ports["wake"] = int(info["wake_port"])

    def _settle_core_port(self) -> None:
        """Re-draw the server's port if the fakes took it while we waited.

        The port was picked before anything was running; the four ephemeral
        binds since then could have landed on it, and a jarvis-core that cannot
        bind fails with an errno rather than with the reason. An explicitly
        requested port is left exactly as asked for — a caller who names a port
        wants that port, and a silent substitution would be worse than the bind
        error.
        """
        taken = {port for name, port in self.ports.items() if name != "core"}
        if self.port not in taken and port_is_free(self.port, self.host):
            return
        if self._port_was_chosen:
            raise HarnessError(
                f"port {self.port} was asked for but is not free "
                f"(the fakes are on {sorted(taken)})"
            )
        self.port = free_port(self.host, avoid=taken)
        self.ports["core"] = self.port

    def _read_child_json(self, child: _Child, path: Path) -> dict[str, Any]:
        """Wait for a fake to report the port it actually bound."""

        def _ready() -> dict[str, Any] | None:
            if not path.is_file():
                return None
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None  # still being written

        return wait_for(_ready, CHILD_TIMEOUT, on_dead=child.dead_reason)

    # --- jarvis-core ------------------------------------------------------
    def _write_config(self) -> None:
        if not (self.core_dir / "jarvis" / "__main__.py").is_file():
            raise HarnessError(f"no jarvis-core at {self.core_dir}")
        # Start from nothing every time. A reused --work-dir would otherwise
        # keep the last run's .storage — registries, and every input helper's
        # value, which the stored copy wins over `initial:` for — so a suite
        # would start from wherever the previous one happened to stop.
        import shutil

        shutil.rmtree(self.config_dir, ignore_errors=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._write_skills()
        (self.config_dir / "configuration.yaml").write_text(
            build_config(
                port=self.port,
                host=self.host,
                ollama_url=self.ollama_url,
                stt_port=self.ports["stt"],
                tts_port=self.ports["tts"],
                wake_port=self.ports["wake"],
                model=self.model,
                wyoming_host=getattr(self, "wyoming_host", self.fake_host),
            ),
            encoding="utf-8",
        )

    def _write_skills(self) -> None:
        """Copy the shipped example skills into the throwaway config.

        Copied rather than pointed at: `skills.reload` and the console both
        write nothing, but a test that edits a skill must not be able to edit
        the repository's own example.
        """
        source = Path(self.core_dir) / "config" / "examples" / "skills"
        if not source.is_dir():
            return
        import shutil

        target = self.config_dir / "skills"
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(
            source, target, ignore=shutil.ignore_patterns("README.md", "__pycache__")
        )

    def _spawn_core(self) -> _Child:
        argv = [
            self.python, "-m", "jarvis",
            "--config", str(self.config_dir),
            "--host", self.host,
            "--port", str(self.port),
        ]
        if self.verbose:
            argv.append("-v")
        env = {
            # jarvis/auth.py: JARVIS_TOKEN overrides the store and is always
            # accepted, so the harness never has to scrape a token out of a log.
            "JARVIS_TOKEN": self.token,
            "PYTHONPATH": os.pathsep.join(
                [str(self.core_dir), os.environ.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep),
        }
        return self._spawn("jarvis-core", argv, env=env, cwd=self.core_dir)

    def _start_core(self) -> None:
        """Boot jarvis-core, re-drawing its port if somebody else took it.

        A port is picked by binding zero and letting go, then handed to the
        server on its command line — there is no way to hand a child a socket
        it did not open. Between the two, any other process on the box may bind
        the same number, and two harnesses started at once really do collide.
        The symptom is an ``EADDRINUSE`` from uvicorn and an exit status, which
        is a fine thing to retry and a terrible thing to fail a suite on.

        Only a bind conflict is retried. A server that starts and then does not
        answer is a real failure and is reported as one, with its own log.
        """
        for attempt in range(1, CORE_BOOT_ATTEMPTS + 1):
            child = self._spawn_core()
            try:
                self._wait_healthy(child)
                return
            except HarnessError:
                retryable = (
                    self._port_was_contended(child)
                    and attempt < CORE_BOOT_ATTEMPTS
                    and not self._port_was_chosen
                )
                child.stop()
                if not retryable:
                    # Leave it in _children so its log is still in info() and
                    # logs(): this is the failure the caller has to read.
                    raise
                self._children.remove(child)
                # Keep the losing log under a name of its own — the next
                # attempt opens `jarvis-core.log` afresh — then take a
                # different port and rebuild the config around it.
                with contextlib.suppress(OSError):
                    child.log.replace(self.log_dir / f"jarvis-core-attempt{attempt}.log")
                self.port = free_port(
                    self.host,
                    avoid={port for name, port in self.ports.items() if name != "core"},
                )
                self.ports["core"] = self.port
                self._write_config()

    def restart_core(self) -> "Harness":
        """Stop jarvis-core and start it again, on the same config and port.

        For the one class of claim that cannot be tested any other way: "it
        remembered". A memory that lives in a process is not a memory, and the
        only honest way to say so is to kill the process. The fakes and the
        config directory are left exactly as they are — this restarts the
        server, not the world.
        """
        for child in list(self._children):
            if child.name.startswith("jarvis-core"):
                child.stop()
                self._children.remove(child)
        self._start_core()
        self._check_token()
        return self

    @staticmethod
    def _port_was_contended(child: _Child) -> bool:
        """Did this jarvis-core die because something already had its port?"""
        if child.alive():
            return False
        log = tail(child.log, 200).lower()
        return "address already in use" in log or "errno 98" in log

    def _core(self) -> _Child:
        for child in self._children:
            if child.name == "jarvis-core":
                return child
        raise HarnessError("jarvis-core was never started")

    def _wait_healthy(self, core: _Child | None = None) -> None:
        core = core if core is not None else self._core()

        def _healthy() -> dict[str, Any] | None:
            try:
                payload = http_json(f"{self.base_url}/healthz", timeout=3.0)
            except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
                return None
            return payload if payload.get("status") == "ok" else None

        try:
            wait_for(_healthy, self.boot_timeout, on_dead=core.dead_reason)
        except HarnessError as err:
            # A boot that timed out with the process still alive says nothing
            # useful on its own; the server's own log always does.
            raise HarnessError(
                f"{err}\njarvis-core never answered /healthz at {self.base_url}. "
                f"Last of {core.log}:\n{tail(core.log)}"
            ) from err

    def _check_token(self) -> None:
        """Prove the deterministic token authenticates before handing it out."""
        core = self._core()

        def _authorised() -> bool:
            try:
                payload = http_json(f"{self.base_url}/api/", token=self.token, timeout=5.0)
            except Exception:  # noqa: BLE001 - retried until the timeout
                return False
            return bool(payload.get("message"))

        wait_for(_authorised, 20.0, on_dead=core.dead_reason)

    # --- steering the fakes ----------------------------------------------
    # Both are cheap, synchronous, loopback calls — safe to make from an
    # async test between awaits, and a great deal clearer than reaching into
    # a subprocess some other way.
    def _control(self, path: str, payload: Any = None) -> Any:
        if self.external_ollama_url:
            # A real model server has no `/_control` plane, and pretending the
            # call worked would let a test "script" a model that then answers
            # however it likes — which is worse than not being able to script
            # it, because the assertions would still be written as if it had.
            raise HarnessError(
                "this harness talks to a real model server "
                f"({self.external_ollama_url}); there is nothing to script"
            )
        return http_json(f"{self.ollama_url}{path}", payload=payload, timeout=5.0)

    def set_ollama_script(self, script: Any = None) -> None:
        """Replace the model's brain. ``None`` restores the default script.

        Rule counters reset, so a test that depends on "tool call first, then
        the answer" always gets the same two rounds.
        """
        self._control("/_control/script", script if script is not None
                      else self.default_ollama_script)

    def reset_ollama(self) -> None:
        """Forget which responses have been served, and what was asked."""
        self._control("/_control/reset", {})

    def ollama_requests(self) -> list[dict[str, Any]]:
        """Every ``/api/chat`` payload the model was sent, in order."""
        return (self._control("/_control/requests") or {}).get("requests", [])

    def last_ollama_messages(self) -> list[dict[str, Any]]:
        requests = self.ollama_requests()
        if not requests:
            raise HarnessError("the model has not been asked anything yet")
        return requests[-1]["payload"].get("messages") or []

    def set_wyoming_script(self, script: dict[str, Any]) -> None:
        """Rewrite the voice fakes' script. They re-read it when it changes."""
        merged = dict(self._wyoming_script_data)
        for role, values in script.items():
            merged[role] = {**merged.get(role, {}), **(values or {})}
        self._wyoming_script_data = merged
        self._write_wyoming_script()

    def set_transcript(self, text: str) -> None:
        """What the fake STT will return next (and from then on)."""
        self.set_wyoming_script({"stt": {"mode": "script", "transcript": text,
                                         "transcripts": []}})

    def set_transcripts(self, texts: list[str]) -> None:
        """A queue of transcripts, one per run; the last one repeats."""
        self.set_wyoming_script({"stt": {"mode": "script", "transcripts": list(texts)}})

    def set_stt_length_mode(self, template: str = "heard {ms} ms of audio") -> None:
        """Derive the transcript from how much audio actually arrived."""
        self.set_wyoming_script({"stt": {"mode": "length", "template": template}})

    def set_wake_detection(self, detect: bool = True, after: int = 2,
                           name: str = "hey_jarvis") -> None:
        self.set_wyoming_script({"wake": {"detect": detect, "detect_after": after,
                                          "name": name}})

    # --- diagnostics ------------------------------------------------------
    def logs(self, lines: int = 60) -> str:
        parts = []
        for child in self._children:
            parts.append(f"--- {child.name} ({child.log}) ---\n{tail(child.log, lines)}")
        return "\n\n".join(parts)

    def check_alive(self) -> None:
        """Raise if anything died. Cheap enough to call between assertions."""
        for child in self._children:
            reason = child.dead_reason()
            if reason:
                raise HarnessError(reason)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Boot the real jarvis-core against fake model/voice backends.",
    )
    parser.add_argument("--host", default="0.0.0.0",
                        help="bind address (0.0.0.0 so an emulator can reach it)")
    parser.add_argument("--fake-host", default="127.0.0.1",
                        help="where the fakes listen (loopback: only jarvis-core needs them)")
    parser.add_argument("--port", type=int, default=None, help="jarvis-core port (default: free)")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="the JARVIS_TOKEN to use")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="model name to advertise")
    parser.add_argument("--work-dir", default=None,
                        help="where config, logs and audio go (default: a temp dir)")
    parser.add_argument("--ollama-script", default=None, help="fake Ollama script JSON")
    parser.add_argument("--wyoming-script", default=None, help="fake Wyoming script JSON")
    parser.add_argument("--transcript", default=None, help="what fake STT always returns")
    parser.add_argument("--stt-mode", default=None, choices=["script", "length"])
    parser.add_argument("--json-out", default=None, help="write the JSON description here too")
    parser.add_argument("--wait", action="store_true", help="stay up until SIGINT/SIGTERM")
    parser.add_argument("--keep", action="store_true", help="do not delete the temp work dir")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    harness = Harness(
        work_dir=args.work_dir,
        port=args.port,
        host=args.host,
        fake_host=args.fake_host,
        token=args.token,
        model=args.model,
        ollama_script=args.ollama_script,
        wyoming_script=args.wyoming_script,
        transcript=args.transcript,
        stt_mode=args.stt_mode,
        keep=args.keep,
        verbose=args.verbose,
    )
    try:
        harness.start()
    except HarnessError as err:
        sys.stderr.write(f"harness: {err}\n")
        return 1

    info = harness.info()
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(info, indent=2), encoding="utf-8")
    # One JSON line on stdout: a test runner reads exactly this.
    print(json.dumps(info), flush=True)

    if not args.wait:
        harness.stop()
        return 0

    stopping = {"now": False}

    def _stop(*_signal: Any) -> None:
        stopping["now"] = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, _stop)

    sys.stderr.write(
        f"harness: up on {harness.base_url} "
        f"(emulator: {harness.emulator_base_url}) — Ctrl-C to stop\n"
    )
    status = 0
    try:
        while not stopping["now"]:
            time.sleep(0.25)
            try:
                harness.check_alive()
            except HarnessError as err:
                sys.stderr.write(f"harness: {err}\n")
                status = 1
                break
    finally:
        harness.stop()
    return status


if __name__ == "__main__":
    sys.exit(main())
