"""The InfluxDB source, against a fake InfluxDB of each generation.

There are three incompatible query languages in the wild and the operator has
whichever their homelab already runs. This checks that the source works out
which one it is talking to and speaks it — offline, against a fake, because the
alternative is a test that only passes on a machine with an InfluxDB.

The live claim is `scripts/check-influx.py`, which is Scripted in
`docs/verification.md`: it needs the operator's own database.
"""

from __future__ import annotations

import httpx

from jarvis.metrics import Window
from jarvis.metrics.sources.influx import InfluxSource, _parse_flux_csv


class FakeJarvis:
    def __init__(self) -> None:
        self.data: dict = {}


def source_with(handler, **settings) -> InfluxSource:
    """A source whose HTTP client is a fake InfluxDB."""
    return InfluxSource(
        FakeJarvis(),
        {"url": "http://influx.test", "bucket": "homelab", **settings},
        transport=httpx.MockTransport(handler),
    )


V2_CSV = """#datatype,string,long,dateTime:RFC3339,double,string,string
,result,table,_time,_value,_field,_measurement
,_result,0,2026-08-24T10:00:00Z,61.5,temp,gpu
,_result,0,2026-08-24T10:01:00Z,62.5,temp,gpu
"""


def v2_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200, json={"name": "influxdb", "status": "pass", "version": "2.7.1"})
    if request.url.path == "/api/v2/query":
        assert request.headers.get("Authorization") == "Token secret-token"
        return httpx.Response(200, text=V2_CSV)
    return httpx.Response(404)


def v1_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(404)
    if request.url.path == "/ping":
        return httpx.Response(204, headers={"X-Influxdb-Version": "1.8.10"})
    if request.url.path == "/query":
        query = request.url.params.get("q", "")
        if "SHOW FIELD KEYS" in query:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "series": [
                                {"name": "gpu", "columns": ["fieldKey"], "values": [["temp"]]}
                            ]
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "series": [
                            {
                                "name": "gpu",
                                "columns": ["time", "mean"],
                                "values": [[1756029600, 61.5], [1756029660, 62.5]],
                            }
                        ]
                    }
                ]
            },
        )
    return httpx.Response(404)


# --- which InfluxDB is it ----------------------------------------------------


async def test_a_2x_server_is_recognised_from_health():
    source = source_with(v2_handler, token="secret-token", org="home")
    assert await source.probe() == ("2", "2.7.1")


async def test_a_1x_server_is_recognised_from_the_ping_header():
    """1.x has no /health; it answers /ping with a version header."""
    source = source_with(v1_handler)
    assert await source.probe() == ("1", "1.8.10")


async def test_the_generation_is_cached_then_re_probed():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            calls["n"] += 1
        return v2_handler(request)

    source = source_with(handler, token="t")
    await source.probe()
    await source.probe()
    assert calls["n"] == 1, "the second probe should have used the cache"
    source._generation = ("2", "2.7.1", 0.0)  # expire it
    await source.probe()
    assert calls["n"] == 2


async def test_nothing_answering_is_a_sentence_an_operator_can_act_on():
    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    source = source_with(dead)
    healthy, why = await source.healthy()
    assert healthy is False
    assert "INFLUX_URL" in why


async def test_a_2x_server_with_no_token_says_so_rather_than_401ing_later():
    source = source_with(v2_handler, token="")
    healthy, why = await source.healthy()
    assert healthy is False and "token" in why.lower()


# --- queries -----------------------------------------------------------------


async def test_flux_is_used_for_2x_and_the_points_come_back():
    source = source_with(v2_handler, token="secret-token", org="home")
    window = Window(start=1756029500, end=1756029700, step=60)
    series = (await source.query(["gpu.temp"], window))[0]
    assert series.error == ""
    assert [point.value for point in series.points] == [61.5, 62.5]


async def test_influxql_is_used_for_1x_and_the_points_come_back():
    source = source_with(v1_handler)
    window = Window(start=1756029500, end=1756029700, step=60)
    series = (await source.query(["gpu.temp"], window))[0]
    assert series.error == ""
    assert [point.value for point in series.points] == [61.5, 62.5]


async def test_a_key_that_is_not_measurement_dot_field_is_refused_not_guessed():
    source = source_with(v2_handler, token="t")
    series = (await source.query(["gpu"], Window.last(60)))[0]
    assert "measurement" in series.error


async def test_a_query_that_fails_is_one_widget_s_problem():
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"version": "2.7.1"})
        return httpx.Response(500, text="boom")

    source = source_with(broken, token="t")
    series = (await source.query(["gpu.temp"], Window.last(600)))[0]
    assert series.error
    assert series.points == []


async def test_the_schema_is_asked_for_rather_than_configured():
    source = source_with(v1_handler)
    keys = [info.key for info in await source.list_series()]
    assert "gpu.temp" in keys


async def test_the_token_never_appears_in_a_url():
    """A token in a query string ends up in every proxy log there is."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return v2_handler(request)

    source = source_with(handler, token="secret-token", org="home")
    await source.query(["gpu.temp"], Window.last(600))
    assert seen, "nothing was requested"
    assert not any("secret-token" in url for url in seen)


# --- the CSV parser ----------------------------------------------------------


def test_flux_csv_ignores_the_annotation_lines():
    rows = _parse_flux_csv(V2_CSV)
    assert len(rows) == 2
    assert rows[0]["_value"] == "61.5"


def test_flux_csv_survives_an_empty_answer():
    assert _parse_flux_csv("") == []
    assert _parse_flux_csv("#datatype,string\n") == []
