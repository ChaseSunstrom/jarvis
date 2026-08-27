"""An InfluxDB the operator already runs, as a data source.

Homelabs measure things: a UPS, a rack's power draw, a GPU's temperature, a
NAS's disk. Those numbers are usually already in an InfluxDB, and asking
somebody to re-plumb them into Jarvis so they can be graphed beside Jarvis's own
would be absurd. So this reads theirs.

## Which InfluxDB

There are three incompatible query languages in the wild and the operator
should not have to know which one they have:

    1.x   InfluxQL over `GET /query?db=…&q=…`
    2.x   Flux over `POST /api/v2/query`, and InfluxQL over `/query` if a DBRP
          mapping exists
    3.x   SQL over `/api/v3/query_sql`, plus a v2-compatible `/api/v2/query`

`probe()` asks `/health` (2.x and 3.x answer with JSON naming their version)
and falls back to `/ping` (1.x answers with an `X-Influxdb-Version` header).
The answer decides the dialect, is cached, and is re-probed after a failure —
an operator upgrading 1.8 to 2.7 under a running Jarvis should not have to
restart it.

## What is NOT done here

No writes. Jarvis reads this database and never puts anything in it; a source
that could write would need a whole permission story, and nothing in a
dashboard wants one.

No cloud. `url` is whatever the operator configured, the token travels in an
`Authorization` header, and there is no default pointing anywhere but
`127.0.0.1`. `metrics: sources:` is the list of what may be reached.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from .. import AGGREGATES, DataSource, Point, Series, SeriesInfo, Window

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8086"
#: A dashboard refresh is not a background job: a source that takes longer than
#: this is one the page should say is slow, not one it should wait for.
TIMEOUT = 8.0
#: How long a generation probe is trusted before asking again.
PROBE_TTL = 300.0
#: The most series one query may ask for. A widget takes eight.
MAX_SERIES = 20

#: `key` is `measurement.field` — the two halves InfluxDB needs and the one
#: string a widget stores. A dot in either half would be ambiguous, so a key
#: with more than one dot is refused rather than guessed at.
KEY_SEPARATOR = "."


class InfluxSource:
    """One InfluxDB. Implements :class:`~jarvis.metrics.DataSource`."""

    name = "influx"

    def __init__(
        self,
        jarvis: "Jarvis",
        options: dict[str, Any] | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = options or {}
        self.jarvis = jarvis
        self.url = str(settings.get("url") or DEFAULT_URL).rstrip("/")
        self.token = str(settings.get("token") or "")
        self.org = str(settings.get("org") or "")
        #: 2.x calls it a bucket, 1.x calls it a database. One field, because an
        #: operator has exactly one of them.
        self.bucket = str(settings.get("bucket") or settings.get("database") or "")
        self.timeout = float(settings.get("timeout") or TIMEOUT)
        self.description = f"InfluxDB at {self.url}"
        #: ("1"|"2"|"3", version string, when it was probed)
        self._generation: tuple[str, str, float] = ("", "", 0.0)
        self._client: httpx.AsyncClient | None = None
        #: Injected by the tests so a fake InfluxDB is reached through the same
        #: client the real one is — headers included, which is how a test can
        #: assert that the token travels in a header and never in a URL.
        self._transport = transport

    # --- plumbing ------------------------------------------------------------
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self.token:
                # A 1.x server ignores it; a 2.x server requires exactly this.
                headers["Authorization"] = f"Token {self.token}"
            self._client = httpx.AsyncClient(
                timeout=self.timeout, headers=headers, transport=self._transport
            )
        return self._client

    async def aclose(self) -> None:  # pragma: no cover - lifecycle
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def probe(self) -> tuple[str, str]:
        """`("2", "2.7.1")` — which InfluxDB this is, cached for [PROBE_TTL]."""
        generation, version, at = self._generation
        if generation and time.time() - at < PROBE_TTL:
            return generation, version
        client = self._http()
        try:
            health = await client.get(f"{self.url}/health")
            if health.status_code < 500:
                body = health.json() if health.content else {}
                version = str(body.get("version") or "")
                if version:
                    self._generation = (version.split(".")[0] or "2", version, time.time())
                    return self._generation[0], version
        except (httpx.HTTPError, ValueError):
            pass
        try:
            ping = await client.get(f"{self.url}/ping")
            version = ping.headers.get("X-Influxdb-Version", "")
            if version:
                self._generation = (version.split(".")[0] or "1", version, time.time())
                return self._generation[0], version
        except httpx.HTTPError:
            pass
        self._generation = ("", "", 0.0)
        return "", ""

    async def healthy(self) -> tuple[bool, str]:
        generation, version = await self.probe()
        if not generation:
            return False, (
                f"nothing answered at {self.url}. Check INFLUX_URL, and that the "
                "database is running and reachable from this machine."
            )
        if generation != "1" and not self.token:
            return False, "this InfluxDB needs a token (INFLUX_TOKEN); none is configured."
        if not self.bucket:
            return False, "no bucket (2.x/3.x) or database (1.x) is configured."
        return True, f"InfluxDB {version}"

    # --- the DataSource protocol --------------------------------------------
    async def list_series(self) -> list[SeriesInfo]:
        """Every `measurement.field` this bucket holds.

        Asked of the server rather than configured by hand: an operator should
        not have to write out the schema of a database they already have.
        """
        generation, _version = await self.probe()
        if not generation:
            return []
        try:
            if generation == "1":
                rows = await self._influxql("SHOW FIELD KEYS")
                pairs = [
                    (str(row.get("_measurement") or row.get("name") or ""), str(row.get("fieldKey") or ""))
                    for row in rows
                ]
            else:
                flux = (
                    f'import "influxdata/influxdb/schema"\n'
                    f'schema.fieldKeys(bucket: "{self.bucket}")'
                )
                rows = await self._flux(flux)
                pairs = [
                    (str(row.get("_measurement") or ""), str(row.get("_value") or ""))
                    for row in rows
                ]
        except Exception as err:  # pragma: no cover - a schema query is not a crash
            _LOGGER.debug("influx: could not list series: %s", err)
            return []

        out: list[SeriesInfo] = []
        for measurement, field in pairs:
            if not field:
                continue
            key = f"{measurement}{KEY_SEPARATOR}{field}" if measurement else field
            out.append(
                SeriesInfo(key=key, label=key, group=measurement or "influx", default_aggregate="mean")
            )
        return sorted(out, key=lambda info: info.key)[:500]

    async def query(self, keys: list[str], window: Window, aggregate: str = "") -> list[Series]:
        generation, _version = await self.probe()
        how = aggregate if aggregate in AGGREGATES else "mean"
        if not generation:
            healthy, why = await self.healthy()
            return [Series(key=key, error=why) for key in keys[:MAX_SERIES]]

        out: list[Series] = []
        for key in keys[:MAX_SERIES]:
            measurement, _, field = key.partition(KEY_SEPARATOR)
            if not measurement or not field or KEY_SEPARATOR in field:
                out.append(
                    Series(key=key, error=f"{key!r} is not measurement{KEY_SEPARATOR}field")
                )
                continue
            try:
                if generation == "1":
                    points = await self._influxql_series(measurement, field, window, how)
                else:
                    points = await self._flux_series(measurement, field, window, how)
                out.append(Series(key=key, label=key, aggregate=how, points=points))
            except httpx.HTTPError as err:
                out.append(Series(key=key, error=f"InfluxDB is unreachable: {err}"[:200]))
            except Exception as err:  # pragma: no cover
                out.append(Series(key=key, error=f"{type(err).__name__}: {err}"[:200]))
        return out

    # --- the two dialects ----------------------------------------------------
    async def _influxql(self, statement: str) -> list[dict[str, Any]]:
        """1.x, and 2.x with a DBRP mapping. Returns flat rows."""
        response = await self._http().get(
            f"{self.url}/query", params={"db": self.bucket, "q": statement, "epoch": "s"}
        )
        response.raise_for_status()
        body = response.json()
        rows: list[dict[str, Any]] = []
        for result in body.get("results") or []:
            for series in result.get("series") or []:
                columns = series.get("columns") or []
                name = series.get("name") or ""
                for values in series.get("values") or []:
                    row = dict(zip(columns, values))
                    row.setdefault("_measurement", name)
                    rows.append(row)
        return rows

    async def _influxql_series(
        self, measurement: str, field: str, window: Window, how: str
    ) -> list[Point]:
        step = int(max(1.0, window.resolved_step()))
        function = {"mean": "MEAN", "min": "MIN", "max": "MAX", "sum": "SUM", "count": "COUNT"}.get(
            how, "LAST" if how == "last" else "MEAN"
        )
        statement = (
            f'SELECT {function}("{field}") FROM "{measurement}" '
            f"WHERE time >= {int(window.start)}s AND time < {int(window.end)}s "
            f"GROUP BY time({step}s) fill(none)"
        )
        rows = await self._influxql(statement)
        points: list[Point] = []
        for row in rows:
            at = row.get("time")
            value = row.get(function.lower(), row.get("value"))
            if at is None:
                continue
            points.append(
                Point(
                    at=float(at),
                    value=float(value) if isinstance(value, (int, float)) else None,
                )
            )
        return points

    async def _flux(self, query: str) -> list[dict[str, Any]]:
        """2.x/3.x. Annotated CSV in, flat rows out."""
        response = await self._http().post(
            f"{self.url}/api/v2/query",
            params={"org": self.org} if self.org else None,
            headers={"Content-Type": "application/vnd.flux", "Accept": "application/csv"},
            content=query,
        )
        response.raise_for_status()
        return _parse_flux_csv(response.text)

    async def _flux_series(
        self, measurement: str, field: str, window: Window, how: str
    ) -> list[Point]:
        step = int(max(1.0, window.resolved_step()))
        function = {"mean": "mean", "min": "min", "max": "max", "sum": "sum", "count": "count"}.get(
            how, "last" if how == "last" else "mean"
        )
        query = (
            f'from(bucket: "{self.bucket}")\n'
            f"  |> range(start: {int(window.start)}, stop: {int(window.end)})\n"
            f'  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}")\n'
            f"  |> aggregateWindow(every: {step}s, fn: {function}, createEmpty: false)\n"
            f'  |> keep(columns: ["_time", "_value"])'
        )
        points: list[Point] = []
        for row in await self._flux(query):
            at = row.get("_time")
            value = row.get("_value")
            if not at:
                continue
            try:
                points.append(Point(at=_iso_to_epoch(str(at)), value=float(value)))
            except (TypeError, ValueError):
                continue
        return points


def _iso_to_epoch(text: str) -> float:
    """RFC3339 → epoch seconds. Flux always answers in UTC."""
    from datetime import datetime

    cleaned = text.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned).timestamp()


def _parse_flux_csv(text: str) -> list[dict[str, Any]]:
    """Annotated CSV → rows, ignoring the annotation lines and blank tables."""
    import csv
    import io

    rows: list[dict[str, Any]] = []
    header: list[str] = []
    for record in csv.reader(io.StringIO(text)):
        if not record or record[0].startswith("#"):
            # `#datatype`, `#group`, `#default` — schema, not data.
            continue
        if not header or record[0] == "" and record[1:2] == ["result"]:
            header = record
            continue
        if len(record) != len(header):
            continue
        rows.append({key: value for key, value in zip(header, record) if key})
    return rows


def build(jarvis: "Jarvis", options: dict[str, Any] | None = None) -> DataSource:
    return InfluxSource(jarvis, options)
