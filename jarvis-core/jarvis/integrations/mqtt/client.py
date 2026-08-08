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
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

DEFAULT_BROKER = "127.0.0.1"
DEFAULT_PORT = 1883
DEFAULT_KEEPALIVE = 60
DEFAULT_QOS = 0


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
    ) -> None:
        self.broker = broker
        self.port = int(port)
        self.username = username
        self.password = password
        self.client_id = client_id or "jarvis"
        self.keepalive = int(keepalive)
        self.will = will
        self.tls = bool(tls)

        self.connected = False
        self.publish_count = 0
        self.message_count = 0
        self._subscriptions: list[_Subscription] = []
        self._broker_subs: dict[str, int] = {}

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
            for topic, _count in list(self._broker_subs.items()):
                await self._safe_backend_subscribe(topic, DEFAULT_QOS)
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
        if first and self.connected:
            await self._safe_backend_subscribe(topic, sub.qos)

        def _unsub() -> None:
            try:
                self._subscriptions.remove(sub)
            except ValueError:
                return
            remaining = self._broker_subs.get(topic, 1) - 1
            if remaining <= 0:
                self._broker_subs.pop(topic, None)
                if self.connected:
                    task = self._backend_unsubscribe(topic)
                    if asyncio.iscoroutine(task):
                        try:
                            asyncio.get_running_loop().create_task(
                                _guard(task, f"unsubscribe {topic}")
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
    ) -> None:
        data = normalize_payload(payload)
        self.publish_count += 1
        try:
            await self._backend_publish(topic, data, bool(retain), int(qos or 0))
        except Exception:
            _LOGGER.exception("Failed publishing to %s", topic)

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

    def dispatch_threadsafe(
        self, loop: asyncio.AbstractEventLoop, message: MqttMessage
    ) -> None:
        """Hand a message from a backend thread back to the event loop."""
        loop.call_soon_threadsafe(
            lambda: loop.create_task(_guard(self.async_dispatch(message), message.topic))
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
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.warning("MQTT connection to %s:%s not ready yet", self.broker, self.port)
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
        while not self._closing:
            try:
                async with self._build_client() as client:
                    self._client = client
                    self.connected = True
                    for topic in list(self._broker_subs):
                        await client.subscribe(topic)
                    self._ready.set()
                    delay = 1.0
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
            except Exception:
                if self._closing:
                    return
                _LOGGER.warning(
                    "MQTT connection lost (%s:%s); retrying in %.0fs",
                    self.broker, self.port, delay, exc_info=True,
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
            _LOGGER.warning("Dropping publish to %s: not connected", topic)
            return
        await self._client.publish(topic, payload=payload, qos=qos, retain=retain)

    async def _backend_disconnect(self) -> None:  # pragma: no cover
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None


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
            return
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
        "client_id": config.get("client_id") or "jarvis",
        "keepalive": config.get("keepalive", DEFAULT_KEEPALIVE),
        "will": will,
        "tls": config.get("tls", False),
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
