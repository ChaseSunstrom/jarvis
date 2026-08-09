"""Live device connections — one websocket, one device, and the seam between them.

The phone, the desktop agent and the satellites all hold a long-lived socket to
``/api/websocket``. Once a socket has said who it is
(``jarvis/device/register``) three things become possible, and this module is
where all three meet:

* **presence** — the device's ``device_event``/``presence`` frames land in
  :class:`jarvis.presence.PresenceRegistry`, which decides where a proactive
  message goes;
* **companion** — :meth:`ConnectedDevices.async_send` is the transport
  ``CompanionManager.set_transport()`` wants: it pushes a ``jarvis_message``
  down that device's live socket and answers ``False`` when the device is not
  there, so the manager queues instead of losing it;
* **actions** — a registered device advertises a manifest of what it can do,
  and :meth:`DeviceLink.dispatch` sends one ``device_command`` and waits for the
  matching ``device_result``.

Two rules are load-bearing here and nowhere else in the server.

**The tier only ever goes up.** :func:`effective_tier` is ``max(local,
requested)``: the action's own tier, from the device's own manifest, is the
floor. An action this server has never heard of is :data:`TIER_CONFIRM` — a
typo or an injected action name can never land in the run-without-asking
bucket. There is deliberately no function in this file that lowers a tier,
reads a policy hint off the wire, or accepts an override flag. The device
enforces the real policy; this side simply never asks for less than the truth.

**A device only speaks for itself.** Every inbound frame is attributed to the
device that socket registered as. A ``device_result`` for a command id this
link never issued is ignored, presence signals are filtered to a fixed
allow-list of measurable facts (so a payload cannot rename a device, mark
itself connected, or overwrite another device's entry), and a socket that never
registers is simply a socket that cannot do any of this — it is not an error
and it must never break the connection.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: ``jarvis.data`` keys this module reads and writes.
DATA_DEVICES = "device_links"
DATA_PRESENCE = "presence"
DATA_COMPANION = "companion"

#: Fired so anything holding a view of the device set (the LLM tool schema, a
#: dashboard) can refresh it without polling.
EVENT_DEVICE_REGISTERED = "jarvis_device_registered"
EVENT_DEVICE_DISCONNECTED = "jarvis_device_disconnected"
EVENT_DEVICE_EVENT = "jarvis_device_event"

TYPE_DEVICE_COMMAND = "device_command"
TYPE_DEVICE_RESULT = "device_result"
TYPE_DEVICE_EVENT = "device_event"
TYPE_MESSAGE_RESULT = "jarvis_message_result"
TYPE_REGISTER = "jarvis/device/register"

PRESENCE_EVENT = "presence"

# --- tiers ------------------------------------------------------------------
#: Runs without asking. Read-only or trivially reversible.
TIER_AUTO = 1
#: Ask once; the user may then let the device remember that answer.
TIER_NOTIFY = 2
#: Asks EVERY time, verbatim. Never remembered, never auto-approved.
TIER_CONFIRM = 3

TIER_NAMES = {TIER_AUTO: "AUTO", TIER_NOTIFY: "NOTIFY", TIER_CONFIRM: "CONFIRM"}

# --- device_result statuses -------------------------------------------------
STATUS_OK = "ok"
STATUS_DENIED = "denied"
STATUS_ERROR = "error"
STATUS_UNSUPPORTED = "unsupported"
VALID_STATUSES = frozenset({STATUS_OK, STATUS_DENIED, STATUS_ERROR, STATUS_UNSUPPORTED})

#: A Tier-3 command can sit on a consent screen for a minute before its action
#: even starts, so this is a watchdog for a device that never answers at all,
#: not a performance budget. It matches the phone's own hard timeout.
DEFAULT_COMMAND_TIMEOUT = 180.0

#: Bounds on one device's advertised manifest, so a hostile or broken client
#: cannot make the server hold (or render into a prompt) an unbounded blob.
MAX_ACTIONS = 200
MAX_CAPABILITIES = 64
MAX_TEXT = 400
MAX_ID = 128


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value if value is not None else "").strip()[:limit]


def parse_tier(value: Any) -> int | None:
    """The wire ``tier`` field, or ``None`` for "no opinion".

    ``None`` is what a malformed, absent or hostile value parses to, and the
    caller then contributes :data:`TIER_AUTO` to the ``max`` — so a bad value
    has exactly two possible outcomes: raise the tier, or change nothing.
    ``bool`` is excluded even though Python calls it an ``int``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            value = int(text)
        else:
            named = {name: tier for tier, name in TIER_NAMES.items()}
            return named.get(text.upper())
    if isinstance(value, float):
        if value != int(value):
            return None
        value = int(value)
    if isinstance(value, int) and value in TIER_NAMES:
        return value
    return None


def effective_tier(local: int, requested: Any = None) -> int:
    """``max(local, requested)``. The only tier arithmetic in the server.

    There is no ``min`` here and there is no branch that returns less than
    ``local``, which is why there is nothing to audit: the server cannot claim
    an action is safer than the device said it was.
    """
    floor = local if local in TIER_NAMES else TIER_CONFIRM
    asked = parse_tier(requested) or TIER_AUTO
    return max(floor, asked)


# --- presence ----------------------------------------------------------------
#: The only presence signals taken off the wire, and the only shapes accepted.
#: ``DevicePresence.update`` sets any attribute it has, so an unfiltered payload
#: could rename a device, mark itself connected, or move its ``device_id`` on
#: top of another entry. Everything here is a fact the device measured about
#: itself; nothing here is identity, and nothing here is authority.
BOOL_SIGNALS = (
    "screen_on",
    "locked",
    "jarvis_foreground",
    "audio_available",
    "driving",
    "muted",
    "charging",
)


def presence_signals(data: Any, now: float | None = None) -> dict[str, Any]:
    """Filter and coerce a ``presence`` event payload into safe signals."""
    if not isinstance(data, dict):
        return {}
    moment = time.time() if now is None else now
    out: dict[str, Any] = {}
    for key in BOOL_SIGNALS:
        value = data.get(key)
        if isinstance(value, bool):
            out[key] = value

    zone = data.get("zone")
    if isinstance(zone, str):
        cleaned = zone.strip()[:64]
        if cleaned:
            out["zone"] = cleaned

    battery = data.get("battery")
    if isinstance(battery, (int, float)) and not isinstance(battery, bool):
        out["battery"] = max(0, min(100, int(battery)))

    interaction = data.get("last_interaction")
    if isinstance(interaction, (int, float)) and not isinstance(interaction, bool):
        if interaction > 0:
            # Clamped to now: a device cannot vote itself permanently ACTIVE by
            # claiming an interaction in the future.
            out["last_interaction"] = min(float(interaction), moment)
    return out


# --- the manifest ------------------------------------------------------------
class DeviceAction:
    """One entry of a device's action manifest, as the server understands it."""

    __slots__ = (
        "id",
        "tier",
        "description",
        "params",
        "capability",
        "available",
        "untrusted_output",
        "unsupported_reason",
    )

    def __init__(
        self,
        id: str,
        tier: int = TIER_CONFIRM,
        description: str = "",
        params: dict[str, str] | None = None,
        capability: str = "",
        available: bool = True,
        untrusted_output: bool = False,
        unsupported_reason: str | None = None,
    ) -> None:
        self.id = id
        self.tier = tier if tier in TIER_NAMES else TIER_CONFIRM
        self.description = description
        self.params = dict(params or {})
        self.capability = capability
        self.available = available
        self.untrusted_output = untrusted_output
        self.unsupported_reason = unsupported_reason

    @classmethod
    def from_manifest(cls, entry: Any) -> "DeviceAction | None":
        """Parse one manifest entry, failing closed on anything odd."""
        if not isinstance(entry, dict):
            return None
        action_id = _text(entry.get("id"), MAX_ID)
        if not action_id:
            return None
        raw_params = entry.get("params")
        params: dict[str, str] = {}
        if isinstance(raw_params, dict):
            for key, value in list(raw_params.items())[:32]:
                params[_text(key, 64)] = _text(value, 200)
        elif isinstance(raw_params, list):
            for key in raw_params[:32]:
                params[_text(key, 64)] = ""
        unsupported = bool(entry.get("unsupported"))
        available = bool(entry.get("available", True)) and not unsupported
        return cls(
            id=action_id,
            # An unparseable tier is CONFIRM, not AUTO: we do not know what this
            # action does, so we must assume the worst about it.
            tier=parse_tier(entry.get("tier")) or TIER_CONFIRM,
            description=_text(entry.get("description")),
            params=params,
            capability=_text(entry.get("capability"), 64),
            available=available,
            untrusted_output=bool(entry.get("untrusted_output")),
            unsupported_reason=_text(entry.get("unsupported_reason")) or None,
        )

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "tier": self.tier,
            "tier_name": TIER_NAMES[self.tier],
            "description": self.description,
            "params": dict(self.params),
            "capability": self.capability,
            "available": self.available,
        }
        if self.untrusted_output:
            payload["untrusted_output"] = True
        if not self.available and self.unsupported_reason:
            payload["unsupported_reason"] = self.unsupported_reason
        return payload


def parse_manifest(actions: Any) -> dict[str, DeviceAction]:
    """``{action_id: DeviceAction}`` from whatever the device sent."""
    if not isinstance(actions, list):
        return {}
    out: dict[str, DeviceAction] = {}
    for entry in actions[:MAX_ACTIONS]:
        action = DeviceAction.from_manifest(entry)
        if action is not None:
            out[action.id] = action
    return out


# --- one connected device ----------------------------------------------------
#: Puts a frame on this device's socket. False means "not delivered".
Sender = Callable[[dict[str, Any]], bool]


class DeviceLink:
    """A registered device on one live socket."""

    def __init__(
        self,
        device_id: str,
        name: str,
        platform: str,
        capabilities: list[str],
        actions: dict[str, DeviceAction],
        sender: Sender,
        app_version: str | None = None,
        owner: Any = None,
    ) -> None:
        self.device_id = device_id
        self.name = name or device_id
        self.platform = platform or "unknown"
        self.capabilities = capabilities
        self.actions = actions
        self.app_version = app_version
        #: The connection object that registered this link, so a stale socket's
        #: teardown cannot evict the device's newer one.
        self.owner = owner
        self.connected = True
        self.registered_at = time.time()
        self._sender: Sender = sender
        self._pending: dict[str, asyncio.Future] = {}

    # --- outbound ---------------------------------------------------------
    def push(self, payload: dict[str, Any]) -> bool:
        """Queue a frame on this device's socket. False if it did not go."""
        if not self.connected:
            return False
        try:
            return bool(self._sender(payload))
        except Exception:  # a dead socket is not this caller's problem
            _LOGGER.debug("Could not push to %s", self.device_id, exc_info=True)
            return False

    def action(self, action_id: str) -> DeviceAction | None:
        return self.actions.get(action_id)

    def tier_for(self, action_id: str) -> int:
        """The device's own tier for an action. Unknown actions are CONFIRM."""
        entry = self.actions.get(action_id)
        return entry.tier if entry is not None else TIER_CONFIRM

    async def dispatch(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        tier: Any = None,
        reason: str = "",
        timeout: float = DEFAULT_COMMAND_TIMEOUT,
    ) -> dict[str, Any]:
        """Send one ``device_command`` and wait for its ``device_result``.

        ``tier`` is a *request*, folded in with :func:`effective_tier`; it can
        only raise what the device's own manifest already said. The device
        applies the same rule again against its real action table, which is the
        authority — this side never decides whether something may run.
        """
        command_id = f"c-{uuid.uuid4().hex[:12]}"
        requested = effective_tier(self.tier_for(action), tier)
        frame = {
            "type": TYPE_DEVICE_COMMAND,
            "command_id": command_id,
            "action": action,
            "params": dict(params or {}),
            "tier": requested,
            # Untrusted text: displayed and logged on the device, never parsed
            # for a decision there or here.
            "reason": reason or "(no reason given)",
        }

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[command_id] = future
        try:
            if not self.push(frame):
                return {
                    "status": STATUS_ERROR,
                    "command_id": command_id,
                    "tier": requested,
                    "error": f"{self.name} is not connected",
                }
            try:
                result = await asyncio.wait_for(asyncio.shield(future), timeout)
            except (asyncio.TimeoutError, TimeoutError):
                return {
                    "status": STATUS_ERROR,
                    "command_id": command_id,
                    "tier": requested,
                    "error": (
                        f"{self.name} did not answer within {timeout:g}s "
                        "(the confirmation prompt may still be on screen)"
                    ),
                }
            except asyncio.CancelledError:
                raise
        finally:
            self._pending.pop(command_id, None)
        result.setdefault("tier", requested)
        return result

    # --- inbound ----------------------------------------------------------
    def on_result(self, frame: Any) -> bool:
        """Resolve the command a ``device_result`` belongs to.

        False for a command id this link never issued — a redelivered, replayed
        or invented result matches nothing and is dropped.
        """
        if not isinstance(frame, dict):
            return False
        command_id = _text(frame.get("command_id"), MAX_ID)
        future = self._pending.get(command_id)
        if future is None or future.done():
            return False
        status = _text(frame.get("status"), 32).lower()
        payload: dict[str, Any] = {
            "command_id": command_id,
            # An unrecognised status is an error: a garbled answer from a device
            # must never read as success.
            "status": status if status in VALID_STATUSES else STATUS_ERROR,
        }
        result = frame.get("result")
        if isinstance(result, dict):
            payload["result"] = result
        elif result is not None:
            payload["result"] = {"value": result}
        error = frame.get("error")
        if error:
            payload["error"] = _text(error, 1000)
        elif payload["status"] not in VALID_STATUSES:
            payload["error"] = "the device answered with no recognised status"
        future.set_result(payload)
        return True

    def abandon(self, reason: str) -> None:
        """Fail every in-flight command; the socket went away."""
        for command_id, future in list(self._pending.items()):
            if not future.done():
                future.set_result(
                    {"command_id": command_id, "status": STATUS_ERROR, "error": reason}
                )
        self._pending.clear()

    def as_dict(self, include_actions: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "device_id": self.device_id,
            "name": self.name,
            "platform": self.platform,
            "capabilities": list(self.capabilities),
            "connected": self.connected,
            "app_version": self.app_version,
            "action_count": len(self.actions),
        }
        if include_actions:
            payload["actions"] = [a.as_dict() for a in self.actions.values()]
        return payload


# --- the hub -----------------------------------------------------------------
class ConnectedDevices:
    """Every device with a live socket, and the way to reach one."""

    def __init__(self, jarvis: "Jarvis") -> None:
        self.jarvis = jarvis
        self.links: dict[str, DeviceLink] = {}

    # --- bookkeeping ------------------------------------------------------
    def register(
        self,
        device_id: str,
        name: str,
        platform: str,
        capabilities: Any,
        actions: Any,
        sender: Sender,
        app_version: Any = None,
        owner: Any = None,
    ) -> DeviceLink:
        """Take (or replace) the connection for ``device_id``."""
        capability_list = [
            _text(c, 64)
            for c in (capabilities if isinstance(capabilities, list) else [])
            if _text(c, 64)
        ][:MAX_CAPABILITIES]
        manifest = parse_manifest(actions)

        previous = self.links.get(device_id)
        if previous is not None and previous.owner is not owner:
            # The device reconnected on a new socket. Nothing in flight on the
            # old one can be answered any more.
            previous.connected = False
            previous.abandon("the device reconnected on another socket")

        link = DeviceLink(
            device_id=device_id,
            name=name,
            platform=platform,
            capabilities=capability_list,
            actions=manifest,
            sender=sender,
            app_version=_text(app_version, 64) or None,
            owner=owner,
        )
        self.links[device_id] = link
        self._fire(EVENT_DEVICE_REGISTERED, link.as_dict(include_actions=False))
        _LOGGER.info(
            "Device registered: %s (%s, %s) with %d action(s)",
            link.name,
            device_id,
            link.platform,
            len(manifest),
        )
        return link

    def disconnect(self, device_id: str, owner: Any = None) -> bool:
        """Drop a device's connection. A stale socket cannot evict a newer one."""
        link = self.links.get(device_id)
        if link is None:
            return False
        if owner is not None and link.owner is not owner:
            return False
        link.connected = False
        link.abandon("the device disconnected before answering")
        del self.links[device_id]
        self._fire(EVENT_DEVICE_DISCONNECTED, {"device_id": device_id, "name": link.name})
        return True

    def get(self, device_id: Any) -> DeviceLink | None:
        return self.links.get(str(device_id or ""))

    def all(self) -> list[DeviceLink]:
        return list(self.links.values())

    def as_dict(self, include_actions: bool = True) -> list[dict[str, Any]]:
        return [link.as_dict(include_actions) for link in self.links.values()]

    # --- the companion transport -----------------------------------------
    async def async_send(self, device_id: str, payload: dict[str, Any]) -> bool:
        """``CompanionManager`` transport: push, or answer False so it queues."""
        link = self.get(device_id)
        if link is None:
            return False
        return link.push(payload)

    def on_result(self, device_id: str, frame: Any) -> bool:
        link = self.get(device_id)
        return bool(link and link.on_result(frame))

    # --- plumbing ---------------------------------------------------------
    def _fire(self, event_type: str, data: dict[str, Any]) -> None:
        try:
            self.jarvis.bus.fire(event_type, data)
        except Exception:  # pragma: no cover - a bad listener must not matter
            _LOGGER.exception("Could not fire %s", event_type)


def get_devices(jarvis: "Jarvis") -> ConnectedDevices:
    """The hub, created on first use so no integration has to own it."""
    hub = jarvis.data.get(DATA_DEVICES)
    if not isinstance(hub, ConnectedDevices):
        hub = ConnectedDevices(jarvis)
        jarvis.data[DATA_DEVICES] = hub
    return hub


def get_presence(jarvis: "Jarvis") -> Any:
    """The presence registry, created on first use.

    The companion integration does the same ``setdefault``; whichever of the
    two arrives first wins and the other joins it.
    """
    presence = jarvis.data.get(DATA_PRESENCE)
    if presence is None:
        from ..presence import PresenceRegistry

        presence = jarvis.data.setdefault(DATA_PRESENCE, PresenceRegistry())
    return presence
