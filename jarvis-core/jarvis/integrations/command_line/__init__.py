"""Command line integration — entities backed by shell commands.

    command_line:
      - sensor:
          name: Disk Free
          command: "df -h / | awk 'NR==2 {print $4}'"
          scan_interval: 300
          command_timeout: 15
          unit_of_measurement: GB
          value_template: "{{ value | replace('G', '') }}"
      - binary_sensor:
          name: VPN Up
          command: "pgrep -x openvpn"
          scan_interval: 60
      - switch:
          name: Server Fan
          command_on: "/usr/local/bin/fan on"
          command_off: "/usr/local/bin/fan off"
          command_state: "/usr/local/bin/fan status"
          value_template: "{{ value == 'on' }}"

Commands run through the shell with a hard timeout; a timed-out or failing
command makes its entity unavailable rather than raising.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
from typing import TYPE_CHECKING, Any

from ...const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from ...entity import Entity, EntityPlatform
from ...helpers.template import render, result_as_boolean

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "command_line"

DEFAULT_TIMEOUT = 15.0
DEFAULT_SCAN_INTERVAL = 60.0

# How long to wait for a killed process to actually go away before giving up
# on collecting its output. SIGKILL is not refusable, so this only covers the
# time it takes the kernel to tear the pipes down.
KILL_GRACE = 5.0

PLATFORM_KEYS = ("sensor", "binary_sensor", "switch")


class CommandFailed(Exception):
    """A command timed out or could not be spawned."""


def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """SIGKILL the command *and* anything it forked.

    The process is spawned with ``start_new_session=True``, so its pgid equals
    its pid and one ``killpg`` reaps the shell plus every grandchild. Killing
    only the shell (``process.kill()``) leaves pipeline members and background
    jobs alive, holding the stdout pipe open forever.
    """
    if hasattr(os, "killpg"):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, OSError):
        process.kill()


async def async_run_command(command: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str]:
    """Run `command` through the shell. Returns (returncode, stdout).

    Raises :class:`CommandFailed` if the command cannot be spawned or does not
    finish within `timeout` seconds.
    """
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own session so a timeout can kill the whole process group.
            start_new_session=True,
        )
    except OSError as exc:
        raise CommandFailed(f"could not run {command!r}: {exc}") from exc

    # NOTE: deliberately *not* asyncio.wait_for(). Cancelling communicate()
    # tears down its pipe readers mid-flight, after which the subprocess
    # transport never reports the pipes as disconnected and process.wait()
    # blocks until the command exits on its own — i.e. the timeout would not
    # be a timeout at all. asyncio.wait() leaves the task running instead, so
    # after killing the process group it settles normally.
    task = asyncio.ensure_future(process.communicate())
    done, _pending = await asyncio.wait({task}, timeout=timeout)

    if not done:
        _kill_process_tree(process)
        finished, _ = await asyncio.wait({task}, timeout=KILL_GRACE)
        if not finished:  # pragma: no cover - the kernel did not free the pipes
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        else:
            task.exception()  # retrieve so asyncio does not log it
        raise CommandFailed(f"{command!r} timed out after {timeout}s")

    try:
        stdout, stderr = task.result()
    except OSError as exc:  # pragma: no cover - pipe died under us
        raise CommandFailed(f"could not read output of {command!r}: {exc}") from exc

    if stderr:
        _LOGGER.debug("%s stderr: %s", command, stderr.decode(errors="replace").strip())
    return process.returncode or 0, stdout.decode(errors="replace").strip()


class CommandLineEntity(Entity):
    """Shared plumbing: run a command, template its output."""

    def __init__(self, jarvis: "Jarvis", config: dict[str, Any]) -> None:
        self.jarvis = jarvis
        self._config = config
        self._attr_name = config.get("name") or "Command Line"
        self._attr_unique_id = config.get("unique_id")
        self._attr_icon = config.get("icon")
        self._attr_device_class = config.get("device_class")
        self._value_template = config.get("value_template")
        self._timeout = float(config.get("command_timeout", DEFAULT_TIMEOUT))
        self._json_attributes = config.get("json_attributes") or []
        self._attr_extra_attributes = {}
        self._attr_should_poll = True

    def _render(self, output: str) -> Any:
        if not self._value_template:
            return output
        return render(self.jarvis, self._value_template, {"value": output})

    def _apply_json_attributes(self, output: str) -> None:
        if not self._json_attributes:
            return
        try:
            payload = json.loads(output)
        except ValueError:
            return
        if isinstance(payload, dict):
            self._attr_extra_attributes = {
                key: payload[key] for key in self._json_attributes if key in payload
            }


class CommandLineSensor(CommandLineEntity):
    def __init__(self, jarvis: "Jarvis", config: dict[str, Any]) -> None:
        super().__init__(jarvis, config)
        self._command = config.get("command") or ""
        self._attr_unit_of_measurement = config.get("unit_of_measurement")

    async def async_update(self) -> None:
        code, output = await async_run_command(self._command, self._timeout)
        if code != 0:
            # Raising is how an entity tells the platform it is unavailable.
            raise CommandFailed(f"{self._command!r} exited {code}")
        value = self._render(output)  # TemplateError -> unavailable
        self._apply_json_attributes(output)
        self._attr_state = value if value != "" else STATE_UNKNOWN


class CommandLineBinarySensor(CommandLineEntity):
    def __init__(self, jarvis: "Jarvis", config: dict[str, Any]) -> None:
        super().__init__(jarvis, config)
        self._command = config.get("command") or ""
        self._payload_on = config.get("payload_on")
        self._payload_off = config.get("payload_off")

    async def async_update(self) -> None:
        code, output = await async_run_command(self._command, self._timeout)
        if self._value_template:
            is_on = result_as_boolean(self._render(output))
        elif self._payload_on is not None or self._payload_off is not None:
            if self._payload_on is not None and output == str(self._payload_on):
                is_on = True
            elif self._payload_off is not None and output == str(self._payload_off):
                is_on = False
            else:
                is_on = code == 0 and bool(output)
        else:
            # No template: a zero exit code with output means "on".
            is_on = code == 0 and bool(output)
        self._apply_json_attributes(output)
        self._attr_state = STATE_ON if is_on else STATE_OFF


class CommandLineSwitch(CommandLineEntity):
    def __init__(self, jarvis: "Jarvis", config: dict[str, Any]) -> None:
        super().__init__(jarvis, config)
        self._command_on = config.get("command_on") or ""
        self._command_off = config.get("command_off") or ""
        self._command_state = config.get("command_state")
        self._attr_should_poll = bool(self._command_state)
        self._attr_state = STATE_OFF

    async def async_update(self) -> None:
        if not self._command_state:
            return
        code, output = await async_run_command(self._command_state, self._timeout)
        if self._value_template:
            is_on = result_as_boolean(self._render(output))
        else:
            is_on = code == 0 and bool(output)
        self._attr_state = STATE_ON if is_on else STATE_OFF

    async def _async_run(self, command: str) -> bool:
        if not command:
            return False
        code, _ = await async_run_command(command, self._timeout)
        if code != 0:
            _LOGGER.warning("%s: command exited %d", self._attr_name, code)
        return code == 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        if await self._async_run(self._command_on):
            self._attr_state = STATE_ON
            self._attr_available = True

    async def async_turn_off(self, **kwargs: Any) -> None:
        if await self._async_run(self._command_off):
            self._attr_state = STATE_OFF
            self._attr_available = True

    async def async_toggle(self, **kwargs: Any) -> None:
        if self._attr_state == STATE_ON:
            await self.async_turn_off()
        else:
            await self.async_turn_on()


_ENTITY_TYPES: dict[str, type[CommandLineEntity]] = {
    "sensor": CommandLineSensor,
    "binary_sensor": CommandLineBinarySensor,
    "switch": CommandLineSwitch,
}


def _as_blocks(config: Any) -> list[dict[str, Any]]:
    if config is None:
        return []
    if isinstance(config, dict):
        return [config]
    return [block for block in config if isinstance(block, dict)]


def _as_entries(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    return [entry for entry in value if isinstance(entry, dict)]


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    blocks = _as_blocks(config)
    if not blocks:
        return True

    store = jarvis.data.setdefault(DOMAIN, {})
    platforms: dict[str, EntityPlatform] = store.setdefault("platforms", {})

    total = 0
    for block in blocks:
        for domain in PLATFORM_KEYS:
            entries = _as_entries(block.get(domain))
            if not entries:
                continue
            scan_interval = float(
                block.get("scan_interval", entries[0].get("scan_interval", DEFAULT_SCAN_INTERVAL))
            )
            platform = platforms.get(domain)
            if platform is None:
                platform = EntityPlatform(jarvis, domain, DOMAIN, scan_interval)
                platforms[domain] = platform

            entities = [_ENTITY_TYPES[domain](jarvis, entry) for entry in entries]
            await platform.async_add_entities(entities, update_before_add=True)
            total += len(entities)

    _LOGGER.info("Command line: %d entities", total)
    return True
