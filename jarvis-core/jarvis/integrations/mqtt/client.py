"""Async MQTT client with a swappable backend.

The rest of the integration only ever talks to :class:`MqttClientBase`:

    await client.async_connect()
    unsub = await client.async_subscribe("tele/+/STATE", callback, qos=0)
    await client.async_publish("cmnd/lamp/POWER", "ON", retain=False, qos=0)
    await client.async_disconnect()

Subscription bookkeeping, MQTT wildcard matching and callback dispatch live in
the base class, so a backend only has to implement four thin hooks:
``_backend_connect`` / ``_backend_subscribe`` / ``_backend_publish`` /
``_backend_disconnect``.

Backends: aiomqtt (preferred) -> paho-mqtt in a thread -> NullClient (logs).
:class:`FakeMqttClient` is the injection point for tests: it records every
publish and lets a test feed messages in without a broker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: Named so a test can drive the clock without patching `time` globally.
_now = time.monotonic

#: The client id used when the operator has not chosen one. See
#: `default_client_id` — the literal string this used to be is why two Jarvises
#: on one broker took each other down.
CLIENT_ID_PREFIX = "jarvis"

DEFAULT_BROKER = "127.0.0.1"
DEFAULT_PORT = 1883
DEFAULT_KEEPALIVE = 60
DEFAULT_QOS = 0

#: A session shorter than this did not "fail to connect" — it connected and was
#: thrown off, which is a different problem with a different fix.
SHORT_SESSION = 10.0

#: How many of those in a row before saying the thing the tracebacks never do.
COLLISION_SESSIONS = 3

#: How long `async_connect` waits for the first successful connection before
#: returning and letting the background runner keep retrying.
#:
#: This is a courtesy, not a requirement: publishes before the link is up are
#: already handled (they log and are dropped), the runner reconnects with
#: backoff forever, and discovery re-subscribes on every reconnect. All the wait
#: buys is that a broker which *is* up — a loopback mosquitto answers in
#: single-digit milliseconds — is connected before setup returns, so the first
#: status publish lands.
#:
#: It used to be a hardcoded ten seconds, which is ten seconds added to every
#: start where the broker is not up yet: a compose stack where mosquitto is
#: still starting, a Pi where the broker unit orders after this one, and every
#: single test that boots the shipped configuration.yaml.
DEFAULT_READY_TIMEOUT = 2.0


@dataclass(slots=True)
class MqttMessage:
    """One received message. Payload is always a (decoded) str."""

    topic: str
    payload: str
    qos: int = 0
    retain: bool = False

    def json(self, default: Any = None) -> Any:
        try:
            return json.loads(self.payload)
        except (ValueError, TypeError):
            return default


MessageCallback = Callable[[MqttMessage], "Awaitable[None] | None"]


@dataclass(slots=True)
class _Subscription:
    topic: str
    callback: MessageCallback
    qos: int = 0


def topic_matches(subscription: str, topic: str) -> bool:
    """MQTT topic filter matching, including ``+`` and ``#`` wildcards."""
    if subscription == topic:
        return True
    sub_parts = subscription.split("/")
    top_parts = topic.split("/")
    for index, part in enumerate(sub_parts):
        if part == "#":
            # '#' matches the remainder, including zero levels, but never a
            # leading $SYS-style topic.
            if index == 0 and top_parts and top_parts[0].startswith("$"):
                return False
            return True
        if index >= len(top_parts):
            return False
        if part == "+":
            if index == 0 and top_parts[0].startswith("$"):
                return False
            continue
        if part != top_parts[index]:
            return False
    return len(sub_parts) == len(top_parts)


def normalize_payload(payload: Any) -> str:
    """Coerce anything publishable into the str we put on the wire."""
    if payload is None:
        return ""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload).decode("utf-8", errors="replace")
    if isinstance(payload, bool):
        return "true" if payload else "false"
    if isinstance(payload, (dict, list, tuple)):
        return json.dumps(payload, separators=(",", ":"), default=str)
    return str(payload)


def default_client_id(seed: str = "") -> str:
    """A client id stable for one installation and different between two.

    MQTT allows one live session per client id: connect a second client with
    the same one and the broker disconnects the first, which reconnects, which
    disconnects the second — a loop that logs a traceback per cycle, moves no
    messages, and is invisible from inside either process because both of them
    believe they are simply reconnecting.

    The literal default (`jarvis`) guaranteed that collision the moment
    anything else ran beside the house's own core: a second box, a dev instance
    next to `docker compose up`, or this repository's test harness — which is
    how it was found, 68 disconnects in three minutes on the live stack while
    every suite in the repository was green.

    Stable rather than random, because a fresh id per start leaves the broker
    holding an abandoned session per restart. Derived from the config directory
    as well as the hostname, because two Jarvises on one host differ by where
    their house lives — and with `network_mode: host` a container's hostname
    *is* the host's, so the hostname alone distinguishes nothing.
    """
    material = f"{socket.gethostname()}:{seed}".encode("utf-8", "replace")
    return f"{CLIENT_ID_PREFIX}-{hashlib.sha1(material).hexdigest()[:8]}"


class MqttClientBase:
    """Backend-agnostic client: bookkeeping, matching and dispatch."""

    backend_name = "base"

    def __init__(
        self,
        broker: str = DEFAULT_BROKER,
        port: int = DEFAULT_PORT,
        username: str | None = None,
        password: str | None = None,
        client_id: str | None = None,
        keepalive: int = DEFAULT_KEEPALIVE,
        will: dict[str, Any] | None = None,
        tls: bool = False,
        ready_timeout: float = DEFAULT_READY_TIMEOUT,
    ) -> None:
        self.broker = broker
        self.port = int(port)
        self.username = username
        self.password = password
        self.client_id = client_id or default_client_id()
        self.keepalive = int(keepalive)
        self.will = will
        self.tls = bool(tls)
        # Clamped at zero: a negative wait would make asyncio.wait_for raise
        # immediately, which is the same as not waiting, but by accident.
        self.ready_timeout = max(0.0, float(ready_timeout))

        self.connected = False
        self.publish_count = 0
        self.publish_failures = 0
        self.message_count = 0
        self._subscriptions: list[_Subscription] = []
        self._broker_subs: dict[str, int] = {}
        # Highest qos any live subscriber asked for, per topic filter. Needed
        # so a reconnect re-subscribes at the qos the caller wanted instead of
        # silently downgrading every subscription to 0.
        self._broker_qos: dict[str, int] = {}
        # asyncio only holds weak references to tasks, so a fire-and-forget
        # task can be garbage-collected mid-flight. Keep strong refs.
        self._background: set[asyncio.Task] = set()

    # --- public API -------------------------------------------------------
    async def async_connect(self) -> bool:
        if self.connected:
            return True
        try:
            ok = await self._backend_connect()
        except Exception:
            _LOGGER.exception("MQTT connect to %s:%s failed", self.broker, self.port)
            return False
        self.connected = ok is not False
        if self.connected:
            for topic in list(self._broker_subs):
                await self._safe_backend_subscribe(
                    topic, self._broker_qos.get(topic, DEFAULT_QOS)
                )
        return self.connected

    async def async_disconnect(self) -> None:
        try:
            await self._backend_disconnect()
        except Exception:
            _LOGGER.exception("Error disconnecting from MQTT broker")
        finally:
            self.connected = False

    async def async_subscribe(
        self,
        topic: str,
        callback: MessageCallback,
        qos: int = DEFAULT_QOS,
    ) -> Callable[[], None]:
        """Subscribe to a topic filter. Returns an unsubscribe callable."""
        sub = _Subscription(topic, callback, int(qos or 0))
        self._subscriptions.append(sub)
        first = self._broker_subs.get(topic, 0) == 0
        self._broker_subs[topic] = self._broker_subs.get(topic, 0) + 1
        upgraded = sub.qos > self._broker_qos.get(topic, -1)
        self._broker_qos[topic] = max(self._broker_qos.get(topic, 0), sub.qos)
        if (first or upgraded) and self.connected:
            await self._safe_backend_subscribe(topic, self._broker_qos[topic])

        def _unsub() -> None:
            try:
                self._subscriptions.remove(sub)
            except ValueError:
                return
            remaining = self._broker_subs.get(topic, 1) - 1
            if remaining <= 0:
                self._broker_subs.pop(topic, None)
                self._broker_qos.pop(topic, None)
                if self.connected:
                    task = self._backend_unsubscribe(topic)
                    if asyncio.iscoroutine(task):
                        try:
                            self._track(
                                asyncio.get_running_loop().create_task(
                                    _guard(task, f"unsubscribe {topic}")
                                )
                            )
                        except RuntimeError:  # no loop: nothing to clean up
                            task.close()
            else:
                self._broker_subs[topic] = remaining

        return _unsub

    async def async_publish(
        self,
        topic: str,
        payload: Any = "",
        retain: bool = False,
        qos: int = DEFAULT_QOS,
    ) -> bool:
        """Publish, returning whether the message actually reached the backend.

        A dropped publish must be visible to the caller: an entity that assumes
        success writes an optimistic state and the UI then shows a lamp as on
        that never received the command.
        """
        data = normalize_payload(payload)
        self.publish_count += 1
        try:
            await self._backend_publish(topic, data, bool(retain), int(qos or 0))
        except ConnectionError as exc:
            # The birth message races the first connection on every start and
            # the retry loop already handles it. A traceback here said
            # "something is broken" about the one case that is by design. The
            # caller still gets False, which is what actually matters.
            _LOGGER.warning("Not publishing to %s yet: %s", topic, exc)
            self.publish_failures += 1
            return False
        except Exception:
            _LOGGER.exception("Failed publishing to %s", topic)
            self.publish_failures += 1
            return False
        return True

    # Short aliases (connect/subscribe/publish/disconnect).
    connect = async_connect
    disconnect = async_disconnect
    subscribe = async_subscribe
    publish = async_publish

    @property
    def subscriptions(self) -> list[str]:
        return sorted(self._broker_subs)

    # --- dispatch ---------------------------------------------------------
    async def async_dispatch(self, message: MqttMessage) -> None:
        """Fan a received message out to every matching subscriber."""
        self.message_count += 1
        for sub in list(self._subscriptions):
            if not topic_matches(sub.topic, message.topic):
                continue
            try:
                result = sub.callback(message)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                _LOGGER.exception(
                    "Error in MQTT callback for %s (sub %s)", message.topic, sub.topic
                )

    def _track(self, task: asyncio.Task) -> asyncio.Task:
        """Hold a strong reference until the task finishes."""
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    def dispatch_threadsafe(
        self, loop: asyncio.AbstractEventLoop, message: MqttMessage
    ) -> None:
        """Hand a message from a backend thread back to the event loop."""
        loop.call_soon_threadsafe(
            lambda: self._track(
                loop.create_task(_guard(self.async_dispatch(message), message.topic))
            )
        )

    # --- backend hooks ----------------------------------------------------
    async def _backend_connect(self) -> bool:
        return True

    async def _backend_disconnect(self) -> None:
        return None

    async def _backend_subscribe(self, topic: str, qos: int) -> None:
        return None

    async def _backend_unsubscribe(self, topic: str) -> None:
        return None

    async def _backend_publish(
        self, topic: str, payload: str, retain: bool, qos: int
    ) -> None:
        return None

    async def _safe_backend_subscribe(self, topic: str, qos: int) -> None:
        try:
            await self._backend_subscribe(topic, qos)
        except Exception:
            _LOGGER.exception("Failed subscribing to %s", topic)


async def _guard(coro: Any, what: str) -> None:
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.exception("Error in MQTT task (%s)", what)


class NullClient(MqttClientBase):
    """No broker available: log publishes, never receive anything."""

    backend_name = "null"

    async def _backend_connect(self) -> bool:
        _LOGGER.warning(
            "No MQTT backend available (install aiomqtt or paho-mqtt); "
            "running in log-only mode"
        )
        return True

    async def _backend_publish(
        self, topic: str, payload: str, retain: bool, qos: int
    ) -> None:
        _LOGGER.info("[mqtt-null] publish %s = %s (retain=%s)", topic, payload, retain)


class FakeMqttClient(MqttClientBase):
    """In-memory client for tests and dry runs.

    Records everything published and lets callers inject inbound messages::

        client = FakeMqttClient()
        await client.async_connect()
        await client.feed("stat/lamp/POWER", "ON")
        assert client.published[-1].topic == "cmnd/lamp/POWER"
    """

    backend_name = "fake"

    def __init__(self, *args: Any, loopback: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.published: list[MqttMessage] = []
        self.loopback = loopback

    async def _backend_connect(self) -> bool:
        return True

    async def _backend_publish(
        self, topic: str, payload: str, retain: bool, qos: int
    ) -> None:
        self.published.append(MqttMessage(topic, payload, qos, retain))
        if self.loopback:
            await self.async_dispatch(MqttMessage(topic, payload, qos, retain))

    async def feed(
        self, topic: str, payload: Any = "", retain: bool = False, qos: int = 0
    ) -> None:
        """Simulate the broker delivering a message."""
        await self.async_dispatch(
            MqttMessage(topic, normalize_payload(payload), qos, retain)
        )

    # --- test conveniences ------------------------------------------------
    def payloads_for(self, topic: str) -> list[str]:
        return [m.payload for m in self.published if m.topic == topic]

    def last_publish(self, topic: str | None = None) -> MqttMessage | None:
        for message in reversed(self.published):
            if topic is None or message.topic == topic:
                return message
        return None

    def clear(self) -> None:
        self.published.clear()


class AiomqttClient(MqttClientBase):
    """aiomqtt backend: a background task owns the connection + message loop."""

    backend_name = "aiomqtt"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: Any = None
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._closing = False

    async def _backend_connect(self) -> bool:
        self._closing = False
        self._task = asyncio.get_running_loop().create_task(self._runner())
        if self.ready_timeout:
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=self.ready_timeout)
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "MQTT connection to %s:%s not ready after %.1fs; continuing, "
                    "the connection keeps retrying in the background",
                    self.broker,
                    self.port,
                    self.ready_timeout,
                )
        return True

    def _build_client(self) -> Any:
        import aiomqtt  # noqa: PLC0415 - optional dependency

        kwargs: dict[str, Any] = {
            "hostname": self.broker,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "keepalive": self.keepalive,
        }
        if self.tls:
            # WITHOUT THIS, `tls: true` was silently ignored on this backend.
            # aiomqtt takes an ssl.SSLContext; it has no boolean flag, so a
            # kwargs dict that never mentions TLS connects in cleartext no
            # matter what the config says — and the username and password
            # above go out with it. The paho fallback honoured `tls` via
            # `tls_set()`, so the same configuration was encrypted or not
            # depending on which optional dependency happened to be installed,
            # with nothing in the logs to say which.
            #
            # `create_default_context()` is what `tls_set()` defaults to:
            # system CA store, hostname checking on, certificate verification
            # required. A broker with a self-signed certificate needs its CA
            # added rather than verification turned off.
            import ssl  # noqa: PLC0415 - only needed on the TLS path

            kwargs["tls_context"] = ssl.create_default_context()

        if self.will:
            try:
                kwargs["will"] = aiomqtt.Will(
                    topic=self.will["topic"],
                    payload=normalize_payload(self.will.get("payload", "offline")),
                    qos=int(self.will.get("qos", 0)),
                    retain=bool(self.will.get("retain", True)),
                )
            except Exception:  # pragma: no cover - version drift
                _LOGGER.debug("Could not build MQTT will message", exc_info=True)
        try:  # aiomqtt >= 2.0
            return aiomqtt.Client(identifier=self.client_id, **kwargs)
        except TypeError:  # pragma: no cover - aiomqtt 1.x
            return aiomqtt.Client(client_id=self.client_id, **kwargs)

    async def _runner(self) -> None:  # pragma: no cover - needs a broker
        delay = 1.0
        failures = 0
        short_sessions = 0
        while not self._closing:
            started = _now()
            connected_here = False
            try:
                async with self._build_client() as client:
                    connected_here = True
                    self._client = client
                    self.connected = True
                    for topic in list(self._broker_subs):
                        await client.subscribe(topic)
                    self._ready.set()
                    messages = client.messages
                    if callable(messages):  # aiomqtt 1.x exposes a method
                        messages = messages()
                    async for message in messages:
                        await self.async_dispatch(
                            MqttMessage(
                                str(message.topic),
                                normalize_payload(message.payload),
                                int(getattr(message, "qos", 0) or 0),
                                bool(getattr(message, "retain", False)),
                            )
                        )
            except asyncio.CancelledError:
                raise
            except Exception as err:
                if self._closing:
                    return
                lived = _now() - started
                if lived >= SHORT_SESSION:
                    # A session that ran for a while and then ended is a fresh
                    # failure, not a continuing one: the backoff starts over
                    # and it earns its traceback. The reset lives HERE rather
                    # than beside `self._ready.set()`, which is where it was:
                    # there, every cycle of a two-second connect-drop loop
                    # counted as a first failure and printed twenty frames.
                    failures, short_sessions, delay = 0, 0, 1.0
                failures += 1
                # Only a session that CONNECTED and then died young is evidence
                # of a collision. A refusal is a short session too — it lasts
                # a millisecond — and three of them in a row is what a broker
                # still booting looks like from a core that started first:
                # the live rig read that as "another Jarvis is evicting this
                # one" (an ERROR, with a traceback) on every stack start.
                short_sessions = (
                    short_sessions + 1 if connected_here and lived < SHORT_SESSION else 0
                )
                if failures == 1 and connected_here:
                    # The first one gets the traceback. The hundredth does not:
                    # a broker that goes away for an hour produced a
                    # twenty-frame trace every second, which is how a log stops
                    # being somewhere anyone looks.
                    _LOGGER.warning(
                        "MQTT connection lost (%s:%s); retrying in %.0fs",
                        self.broker, self.port, delay, exc_info=True,
                    )
                elif failures == 1:
                    # Never connected: the reason is the one line, not a stack.
                    _LOGGER.warning(
                        "MQTT broker not reachable (%s:%s): %s; retrying in %.0fs",
                        self.broker, self.port, err, delay,
                    )
                else:
                    _LOGGER.warning(
                        "MQTT still down (%s:%s), attempt %d; retrying in %.0fs",
                        self.broker, self.port, failures, delay,
                    )
                if short_sessions == COLLISION_SESSIONS:
                    # The signature of two clients sharing one id: the
                    # connection succeeds and dies seconds later, forever,
                    # because the other one is reconnecting and evicting this
                    # session exactly as this one evicts theirs. Said plainly,
                    # once, because no amount of reading the tracebacks reveals
                    # it — they only ever say "disconnected".
                    _LOGGER.error(
                        "MQTT connected and dropped %d times in a row within %.0fs each. "
                        "Client id %r is almost certainly in use by another Jarvis on "
                        "this broker — they are evicting each other. Set a different "
                        "`mqtt: client_id:` on one of them.",
                        short_sessions, SHORT_SESSION, self.client_id,
                    )
            finally:
                self._client = None
                self.connected = False
            if self._closing:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    async def _backend_subscribe(self, topic: str, qos: int) -> None:  # pragma: no cover
        if self._client is not None:
            await self._client.subscribe(topic, qos=qos)

    async def _backend_unsubscribe(self, topic: str) -> None:  # pragma: no cover
        if self._client is not None:
            await self._client.unsubscribe(topic)

    async def _backend_publish(  # pragma: no cover - needs a broker
        self, topic: str, payload: str, retain: bool, qos: int
    ) -> None:
        if self._client is None:
            # Raise rather than return: async_publish turns this into a False,
            # which is what stops callers writing optimistic state for a
            # command that never left the box.
            raise ConnectionError(f"not connected to {self.broker}:{self.port}")
        await self._client.publish(topic, payload=payload, qos=qos, retain=retain)

    async def _backend_disconnect(self) -> None:  # pragma: no cover
        self._closing = True
        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Only swallow the runner's own cancellation. If *we* were the
                # ones cancelled, that has to keep propagating (3.11 uncancel
                # bookkeeping tells the two apart).
                current = asyncio.current_task()
                if current is not None and current.cancelling() > 0:
                    raise
            except Exception:
                _LOGGER.debug("MQTT runner ended with an error", exc_info=True)


class PahoMqttClient(MqttClientBase):
    """paho-mqtt backend: the network loop runs in paho's own thread."""

    backend_name = "paho"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def _backend_connect(self) -> bool:  # pragma: no cover - needs a broker
        import paho.mqtt.client as paho  # noqa: PLC0415 - optional dependency

        self._loop = asyncio.get_running_loop()
        try:  # paho 2.x
            client = paho.Client(
                paho.CallbackAPIVersion.VERSION1, client_id=self.client_id
            )
        except (AttributeError, TypeError):  # paho 1.x
            client = paho.Client(client_id=self.client_id)
        if self.username:
            client.username_pw_set(self.username, self.password)
        if self.tls:
            client.tls_set()
        if self.will:
            client.will_set(
                self.will["topic"],
                normalize_payload(self.will.get("payload", "offline")),
                qos=int(self.will.get("qos", 0)),
                retain=bool(self.will.get("retain", True)),
            )

        def _on_connect(_c: Any, _u: Any, _f: Any, _rc: Any, *_a: Any) -> None:
            for topic in list(self._broker_subs):
                client.subscribe(topic)

        def _on_message(_c: Any, _u: Any, msg: Any) -> None:
            if self._loop is None:
                return
            self.dispatch_threadsafe(
                self._loop,
                MqttMessage(
                    msg.topic,
                    normalize_payload(msg.payload),
                    int(getattr(msg, "qos", 0) or 0),
                    bool(getattr(msg, "retain", False)),
                ),
            )

        client.on_connect = _on_connect
        client.on_message = _on_message
        self._client = client
        await asyncio.to_thread(client.connect, self.broker, self.port, self.keepalive)
        client.loop_start()
        return True

    async def _backend_subscribe(self, topic: str, qos: int) -> None:  # pragma: no cover
        if self._client is not None:
            self._client.subscribe(topic, qos=qos)

    async def _backend_unsubscribe(self, topic: str) -> None:  # pragma: no cover
        if self._client is not None:
            self._client.unsubscribe(topic)

    async def _backend_publish(  # pragma: no cover
        self, topic: str, payload: str, retain: bool, qos: int
    ) -> None:
        if self._client is None:
            raise ConnectionError(f"not connected to {self.broker}:{self.port}")
        self._client.publish(topic, payload=payload, qos=qos, retain=retain)

    async def _backend_disconnect(self) -> None:  # pragma: no cover
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None


def available_backends() -> list[str]:
    """Which real backends can be imported right now."""
    found = []
    for name, module in (("aiomqtt", "aiomqtt"), ("paho", "paho.mqtt.client")):
        try:
            __import__(module)
        except ImportError:
            continue
        found.append(name)
    return found


def create_client(config: dict[str, Any]) -> MqttClientBase:
    """Build the best available client for a YAML `mqtt:` block."""
    will = config.get("will_message")
    if not will and config.get("will_topic"):
        will = {
            "topic": config["will_topic"],
            "payload": config.get("will_payload", "offline"),
            "retain": config.get("will_retain", True),
            "qos": config.get("will_qos", 0),
        }
    kwargs: dict[str, Any] = {
        "broker": config.get("broker") or config.get("host") or DEFAULT_BROKER,
        "port": config.get("port", DEFAULT_PORT),
        "username": config.get("username"),
        "password": config.get("password"),
        "client_id": config.get("client_id") or default_client_id(),
        "keepalive": config.get("keepalive", DEFAULT_KEEPALIVE),
        "will": will,
        "tls": config.get("tls", False),
        "ready_timeout": config.get("ready_timeout", DEFAULT_READY_TIMEOUT),
    }

    backend = str(config.get("backend", "auto")).lower()
    order = [backend] if backend != "auto" else ["aiomqtt", "paho", "null"]
    available = available_backends()
    for name in order:
        if name in ("aiomqtt", "paho") and name not in available:
            continue
        if name == "aiomqtt":
            return AiomqttClient(**kwargs)
        if name == "paho":
            return PahoMqttClient(**kwargs)
        if name == "fake":
            return FakeMqttClient(**kwargs)
        if name == "null":
            return NullClient(**kwargs)
    return NullClient(**kwargs)
