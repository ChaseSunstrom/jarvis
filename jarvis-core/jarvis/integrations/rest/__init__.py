"""REST integration — sensors, binary sensors and switches from any HTTP API.

    rest:
      - resource: http://10.0.0.5/api/status
        scan_interval: 30
        method: GET
        headers:
          Authorization: !secret api_token
        sensor:
          - name: Solar Power
            value_template: "{{ value_json.power }}"
            unit_of_measurement: W
            device_class: power
            json_attributes: [voltage, current]
        binary_sensor:
          - name: Grid Online
            value_template: "{{ value_json.grid == 'up' }}"
        switch:
          - name: Garden Pump
            resource: http://10.0.0.5/api/pump
            body_on: '{"on": true}'
            body_off: '{"on": false}'
            is_on_template: "{{ value_json.on }}"

One HTTP request serves every entity in a block: the first entity added for
a resource does the polling and pushes the shared payload to its siblings.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from ...const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from ...entity import Entity, EntityPlatform
from ...helpers.template import (
    TemplateError,
    is_template,
    render,
    result_as_boolean,
)

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "rest"

DEFAULT_METHOD = "GET"
DEFAULT_SCAN_INTERVAL = 30.0
DEFAULT_TIMEOUT = 10.0
DEFAULT_SWITCH_METHOD = "POST"
DEFAULT_BODY_ON = "ON"
DEFAULT_BODY_OFF = "OFF"

PLATFORM_KEYS = ("sensor", "binary_sensor", "switch")


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------
def create_client(jarvis: "Jarvis", verify_ssl: bool = True, timeout: float = DEFAULT_TIMEOUT):
    """Build the shared AsyncClient, honouring test injection.

    Tests seed ``jarvis.data["rest"] = {"transport": httpx.MockTransport(...)}``
    (or a ready-made ``"client"``) before calling ``async_setup``.
    """
    store = jarvis.data.setdefault(DOMAIN, {})
    client = store.get("client")
    if client is not None:
        store.setdefault("owns_client", False)
        return client
    transport = store.get("transport")
    client = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(timeout),
        verify=verify_ssl if transport is None else True,
        follow_redirects=True,
    )
    store["client"] = client
    store["owns_client"] = True
    return client


class RestData:
    """One HTTP endpoint plus the last payload fetched from it."""

    def __init__(
        self,
        jarvis: "Jarvis",
        client: httpx.AsyncClient,
        resource: str,
        method: str = DEFAULT_METHOD,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        payload: Any = None,
        auth: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.jarvis = jarvis
        self.client = client
        self.resource = resource
        self.method = (method or DEFAULT_METHOD).upper()
        self.headers = dict(headers or {})
        self.params = dict(params or {})
        self.payload = payload
        self.auth = auth
        self.timeout = timeout

        self.data: str | None = None
        self.json: Any = None
        self.status_code: int | None = None
        self.last_error: str | None = None
        self.subscribers: list["RestEntity"] = []

    # -- subscribers -------------------------------------------------------
    def register(self, entity: "RestEntity") -> bool:
        """Register an entity; returns True if it should drive the polling."""
        self.subscribers.append(entity)
        return len(self.subscribers) == 1

    def notify_subscribers(self, exclude: "RestEntity | None" = None) -> None:
        for entity in self.subscribers:
            if entity is exclude:
                continue
            entity.apply_data()
            entity.async_write_state()

    # -- fetching ----------------------------------------------------------
    def _url(self) -> str:
        if is_template(self.resource):
            return render(self.jarvis, self.resource)
        return self.resource

    async def async_update(self) -> None:
        """Fetch the endpoint. Raises on transport/HTTP failure."""
        body = self.payload
        if is_template(body):
            body = render(self.jarvis, body)

        kwargs: dict[str, Any] = {
            "headers": self.headers or None,
            "params": self.params or None,
        }
        if self.auth is not None:
            kwargs["auth"] = self.auth
        if body is not None:
            if isinstance(body, (dict, list)):
                kwargs["json"] = body
            else:
                kwargs["content"] = str(body)

        try:
            response = await self.client.request(self.method, self._url(), **kwargs)
        except httpx.HTTPError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        self.status_code = response.status_code
        self.data = response.text
        try:
            self.json = response.json()
        except ValueError:
            self.json = None
        if response.status_code >= 400:
            self.last_error = f"HTTP {response.status_code}"
            raise httpx.HTTPStatusError(
                self.last_error, request=response.request, response=response
            )
        self.last_error = None


def _extract_path(payload: Any, path: str | None) -> Any:
    """Walk a dotted path into a parsed JSON payload."""
    if not path:
        return payload
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            index = int(part)
            current = current[index] if -len(current) <= index < len(current) else None
        else:
            return None
    return current


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------
class RestEntity(Entity):
    """Shared behaviour: render from the block's payload, own the polling."""

    def __init__(self, jarvis: "Jarvis", rest: RestData, config: dict[str, Any]) -> None:
        self.jarvis = jarvis
        self._rest = rest
        self._config = config
        self._attr_name = config.get("name") or "REST"
        self._attr_unique_id = config.get("unique_id") or None
        self._attr_icon = config.get("icon")
        self._attr_device_class = config.get("device_class")
        self._value_template = config.get("value_template")
        self._json_attributes = config.get("json_attributes") or []
        self._json_attributes_path = config.get("json_attributes_path")
        self._attr_extra_attributes = {}
        self._attr_should_poll = False
        self._is_poller = False
        self._last_error: Exception | None = None

    # -- template helpers --------------------------------------------------
    def _template_vars(self) -> dict[str, Any]:
        return {"value": self._rest.data, "value_json": self._rest.json}

    def _render_value(self) -> Any:
        if not self._value_template:
            return self._rest.data
        return render(self.jarvis, self._value_template, self._template_vars())

    def _apply_json_attributes(self) -> None:
        if not self._json_attributes:
            return
        payload = _extract_path(self._rest.json, self._json_attributes_path)
        if not isinstance(payload, dict):
            return
        self._attr_extra_attributes = {
            key: payload[key] for key in self._json_attributes if key in payload
        }

    def apply_data(self) -> None:
        """Recompute this entity's state from the shared payload."""
        try:
            self._attr_state = self._compute_state()
            self._apply_json_attributes()
            self._attr_available = True
            self._last_error = None
        except TemplateError as exc:
            _LOGGER.warning("%s: %s", self._attr_name, exc)
            self._attr_available = False
            self._last_error = exc

    def _compute_state(self) -> Any:
        raise NotImplementedError

    # -- polling -----------------------------------------------------------
    async def async_update(self) -> None:
        """Fetch (poller only) and recompute; siblings ride the same payload."""
        if self._is_poller or self._rest.data is None:
            await self._rest.async_update()
            self.apply_data()
            self._rest.notify_subscribers(exclude=self)
        else:
            self.apply_data()
        if self._last_error is not None:
            # Let the platform mark this entity unavailable.
            raise self._last_error


class RestSensor(RestEntity):
    def __init__(self, jarvis: "Jarvis", rest: RestData, config: dict[str, Any]) -> None:
        super().__init__(jarvis, rest, config)
        self._attr_unit_of_measurement = config.get("unit_of_measurement")

    def _compute_state(self) -> Any:
        value = self._render_value()
        return STATE_UNKNOWN if value is None else value


class RestBinarySensor(RestEntity):
    def _compute_state(self) -> Any:
        value = self._render_value()
        if value is None:
            return STATE_UNKNOWN
        return STATE_ON if result_as_boolean(value) else STATE_OFF


class RestSwitch(RestEntity):
    """A switch that POSTs a body to turn on/off and reads back its state."""

    def __init__(
        self,
        jarvis: "Jarvis",
        rest: RestData,
        config: dict[str, Any],
        client: httpx.AsyncClient,
        command_resource: str,
        command_method: str,
        headers: dict[str, str] | None,
        auth: Any,
    ) -> None:
        super().__init__(jarvis, rest, config)
        self._client = client
        self._command_resource = command_resource
        self._command_method = (command_method or DEFAULT_SWITCH_METHOD).upper()
        self._headers = dict(headers or {})
        self._auth = auth
        self._body_on = config.get("body_on", DEFAULT_BODY_ON)
        self._body_off = config.get("body_off", DEFAULT_BODY_OFF)
        self._is_on_template = config.get("is_on_template") or config.get("value_template")
        self._attr_state = STATE_OFF

    def _compute_state(self) -> Any:
        if not self._is_on_template:
            # No readback template: trust the last command we sent.
            return self._attr_state
        rendered = render(self.jarvis, self._is_on_template, self._template_vars())
        return STATE_ON if result_as_boolean(rendered) else STATE_OFF

    async def _async_send(self, body: Any) -> None:
        if is_template(body):
            body = render(self.jarvis, body)
        url = self._command_resource
        if is_template(url):
            url = render(self.jarvis, url)
        kwargs: dict[str, Any] = {"headers": self._headers or None}
        if self._auth is not None:
            kwargs["auth"] = self._auth
        if body is not None:
            if isinstance(body, (dict, list)):
                kwargs["json"] = body
            else:
                kwargs["content"] = str(body)
        response = await self._client.request(self._command_method, url, **kwargs)
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {response.status_code}", request=response.request, response=response
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_send(self._body_on)
        self._attr_state = STATE_ON
        self._attr_available = True

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_send(self._body_off)
        self._attr_state = STATE_OFF
        self._attr_available = True

    async def async_toggle(self, **kwargs: Any) -> None:
        if self._attr_state == STATE_ON:
            await self.async_turn_off()
        else:
            await self.async_turn_on()


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
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


def _auth_from(block: dict[str, Any]) -> Any:
    username, password = block.get("username"), block.get("password")
    if username is None and password is None:
        return None
    return httpx.BasicAuth(str(username or ""), str(password or ""))


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    blocks = _as_blocks(config)
    if not blocks:
        return True

    store = jarvis.data.setdefault(DOMAIN, {})
    platforms: dict[str, EntityPlatform] = store.setdefault("platforms", {})
    resources: list[RestData] = store.setdefault("resources", [])

    verify_ssl = all(block.get("verify_ssl", True) for block in blocks)
    timeout = max(float(block.get("timeout", DEFAULT_TIMEOUT)) for block in blocks)
    client = create_client(jarvis, verify_ssl=verify_ssl, timeout=timeout)

    if store.get("owns_client", True) and not store.get("shutdown_registered"):
        store["shutdown_registered"] = True
        jarvis.register_shutdown(client.aclose)

    total = 0
    for index, block in enumerate(blocks):
        resource = block.get("resource")
        if not resource and not any(
            entry.get("resource")
            for key in PLATFORM_KEYS
            for entry in _as_entries(block.get(key))
        ):
            _LOGGER.error("rest block #%d has no 'resource'; skipping", index)
            continue

        scan_interval = float(block.get("scan_interval", DEFAULT_SCAN_INTERVAL))
        headers = block.get("headers") or {}
        auth = _auth_from(block)
        block_timeout = float(block.get("timeout", DEFAULT_TIMEOUT))

        shared: RestData | None = None
        if resource:
            shared = RestData(
                jarvis,
                client,
                resource,
                method=block.get("method", DEFAULT_METHOD),
                headers=headers,
                params=block.get("params"),
                payload=block.get("payload"),
                auth=auth,
                timeout=block_timeout,
            )
            resources.append(shared)

        for domain in PLATFORM_KEYS:
            entries = _as_entries(block.get(domain))
            if not entries:
                continue
            platform = platforms.get(domain)
            if platform is None:
                platform = EntityPlatform(jarvis, domain, DOMAIN, scan_interval)
                platforms[domain] = platform

            new_entities: list[Entity] = []
            for entry in entries:
                entity = _build_entity(
                    jarvis, domain, entry, block, shared, client, headers, auth, block_timeout
                )
                if entity is None:
                    continue
                if entity._rest.register(entity):
                    entity._is_poller = True
                    entity._attr_should_poll = True
                new_entities.append(entity)

            if new_entities:
                total += len(new_entities)
                await platform.async_add_entities(new_entities, update_before_add=True)

    _LOGGER.info("REST: %d entities across %d blocks", total, len(blocks))
    return True


def _build_entity(
    jarvis: "Jarvis",
    domain: str,
    entry: dict[str, Any],
    block: dict[str, Any],
    shared: RestData | None,
    client: httpx.AsyncClient,
    headers: dict[str, str],
    auth: Any,
    timeout: float,
) -> RestEntity | None:
    own_resource = entry.get("resource")

    if domain == "switch":
        state_resource = entry.get("state_resource") or own_resource or block.get("resource")
        command_resource = own_resource or block.get("resource")
        if not command_resource:
            _LOGGER.error("rest switch %r has no resource", entry.get("name"))
            return None
        # Switches with their own state endpoint poll it themselves; those
        # sharing the block resource ride along with the block payload.
        if shared is not None and state_resource == block.get("resource"):
            rest = shared
        else:
            rest = RestData(
                jarvis,
                client,
                state_resource or command_resource,
                method=entry.get("state_method", DEFAULT_METHOD),
                headers=headers,
                auth=auth,
                timeout=timeout,
            )
        return RestSwitch(
            jarvis,
            rest,
            entry,
            client,
            command_resource,
            entry.get("method", DEFAULT_SWITCH_METHOD),
            headers,
            auth,
        )

    if own_resource and own_resource != block.get("resource"):
        rest = RestData(
            jarvis,
            client,
            own_resource,
            method=entry.get("method", block.get("method", DEFAULT_METHOD)),
            headers=headers,
            params=block.get("params"),
            payload=block.get("payload"),
            auth=auth,
            timeout=timeout,
        )
    elif shared is not None:
        rest = shared
    else:
        _LOGGER.error("rest %s %r has no resource", domain, entry.get("name"))
        return None

    if domain == "sensor":
        return RestSensor(jarvis, rest, entry)
    return RestBinarySensor(jarvis, rest, entry)
