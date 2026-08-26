"""The sky: ISS passes, what is overhead, the moon, the planets — offline.

Everything here runs against a fixed element set (a real ISS OMM row fetched
from CelesTrak on 2026-08-26 as CSV, epoch 2026-08-25 15:51 UTC; the same set
as a hand-typed TLE for the fallback reader), a three-month excerpt of de421
(36 KB, cut with ``python -m jplephem excerpt 2026/7/15 2026/10/15``), a
frozen clock and London's coordinates. The network is pinned shut for the
whole module: ``httpx.AsyncClient`` is replaced by a class that raises, so a
fetch that slipped past ``download: false`` fails the test that triggered it
rather than reaching CelesTrak.

The numbers asserted are what skyfield computes from these inputs against the
FULL de421 — the excerpt was checked to give byte-identical output before it
was committed — and they were sanity-checked against the world: a 51.6°
orbit over London passes every ~93 minutes in the small hours; the full moon
of 2026-08-28 04:18 UTC is in every almanac; the 2026-08-12 new moon is the
one with the total solar eclipse.

What is NOT asserted: that a pass is visible on any particular real night
(that is the sky's business, and the live scenario `sky-iss-pass` is the one
that talks to the running stack), and any number closer than a minute.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.automation.util import DATA_CLOCK  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import sky  # noqa: E402
from jarvis.integrations.sky import (  # noqa: E402
    MIN_REFRESH_HOURS,
    Ephemeris,
    SkyData,
    TleCache,
    async_setup,
    compass,
    moon_phase_name,
    parse_elements,
)
from jarvis.llm.tools import TIER_DIRECT, ToolRegistry  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
#: The element set the tests are written against: CelesTrak's OMM CSV row.
OMM = FIXTURES / "tle" / "iss.csv"
#: The same set as a TLE — what a person types in by hand, and the fallback reader.
TLE = FIXTURES / "tle" / "iss.tle"
EPHEMERIS = FIXTURES / "ephemeris" / "de421-2026q3.bsp"
SOURCE = "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=csv"

LONDON = ZoneInfo("Europe/London")
#: A Wednesday evening, BST, a day after the element set's epoch.
NOW = datetime(2026, 8, 26, 18, 0, tzinfo=LONDON)
LATITUDE, LONGITUDE, ELEVATION = 51.5072, -0.1276, 11.0


# --- fixtures -------------------------------------------------------------------
@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing in this module may open a client. A fetch is a failed test, not a slow one."""

    class Refuse:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("the sky tests must not open a network client")

    monkeypatch.setattr(httpx, "AsyncClient", Refuse)


class FrozenClock:
    """A clock a test moves by hand; `now()` so it can stand in for the house clock."""

    def __init__(self, at: datetime = NOW) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def now(self) -> datetime:
        return self.at

    def advance(self, **kw: Any) -> None:
        self.at += timedelta(**kw)


def seed_cache(directory: Path, fetched_at: datetime = NOW - timedelta(hours=12),
               name: str = "stations") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(OMM, directory / f"{name}.csv")
    (directory / f"{name}.meta.json").write_text(
        json.dumps({"url": SOURCE, "fetched_at": fetched_at.isoformat()}), encoding="utf-8"
    )
    return directory


@pytest.fixture
def cache_dir(tmp_path):
    return seed_cache(tmp_path / "tle")


def make_sky(cache_dir: Path, clock: FrozenClock | None = None, ephemeris: Path = EPHEMERIS,
             download: bool = False, fetch: Any = None, sources: list[str] | None = None,
             **kw: Any) -> SkyData:
    clock = clock or FrozenClock()
    cache = TleCache(cache_dir, sources=sources if sources is not None else [SOURCE],
                     download=download, fetch=fetch, now=clock)
    return SkyData(
        LATITUDE, LONGITUDE, ELEVATION,
        cache=cache,
        ephemeris=Ephemeris(ephemeris, download=download),
        now=clock,
        **kw,
    )


def _dt(value: str | None) -> datetime:
    assert value, "expected an ISO time"
    return datetime.fromisoformat(value)


# --- passes ----------------------------------------------------------------------
def test_a_pass_is_found_within_48_hours_with_sane_numbers(cache_dir):
    result = make_sky(cache_dir).next_pass("ISS", 48)

    assert result["status"] == "ok", result
    assert result["satellite"] == "ISS (ZARYA)"
    first = result["pass"]
    assert first is not None
    rise, culmination, setting = _dt(first["rise"]), _dt(first["culmination"]), _dt(first["set"])
    assert NOW < rise < culmination < setting < NOW + timedelta(hours=48)
    # A low-orbit pass above 10° is a few minutes, never a few seconds or an hour.
    assert 60 <= first["duration_s"] <= 720
    assert 10 <= first["max_altitude"] <= 90
    for key in ("rise_direction", "direction", "set_direction"):
        assert first[key] in sky.COMPASS, first[key]
    # Every ~93 minutes when the geometry allows: a handful of passes in two
    # days, not one and not fifty.
    assert 3 <= result["passes_in_window"] <= 20
    # The caveat every answer carries, both ways of measuring it.
    assert result["tle_age_hours"] == 12.0
    assert 1.0 <= result["elements_age_days"] <= 1.1
    assert "elements" in result["spoken"] and "old" in result["spoken"]


def test_the_first_pass_and_the_first_visible_pass_are_different_things(cache_dir):
    """01:35 is a real pass — low, in the Earth's shadow, and nobody will see it.

    The visible one is 04:45: the station comes out of shadow high over the
    house while the sky is still dark. An answer that gave the first would
    send someone outside for nothing.
    """
    result = make_sky(cache_dir).next_pass("ISS", 48)

    first, visible = result["pass"], result["next_visible"]
    assert first["visible"] is False and first["sunlit"] is False
    assert first["rise"].startswith("2026-08-27T01:35")
    assert visible is not None and visible["visible"] is True
    assert visible["rise"].startswith("2026-08-27T04:45")
    assert visible["max_altitude"] >= 80 and visible["bright"] is True
    assert visible["rise_direction"] == "west" and visible["set_direction"] == "east"
    assert result["visible_in_window"] >= 1
    spoken = result["spoken"]
    assert "04:45" in spoken and "west" in spoken and "bright" in spoken
    assert "tomorrow morning" in spoken


def test_times_are_in_the_house_zone(cache_dir):
    london = make_sky(cache_dir).next_pass("ISS", 48)["pass"]
    assert london["rise"].endswith("+01:00"), london["rise"]

    kathmandu = FrozenClock(NOW.astimezone(ZoneInfo("Asia/Kathmandu")))
    result = make_sky(cache_dir, clock=kathmandu).next_pass("ISS", 48)["pass"]
    assert result["rise"].endswith("+05:45"), result["rise"]
    # The same instant, told in a different zone.
    assert _dt(result["rise"]) == _dt(london["rise"])


def test_a_satellite_can_be_asked_for_by_what_people_call_it(cache_dir):
    data = make_sky(cache_dir)
    for query in ("ISS", "iss", "the space station", "ISS (ZARYA)", "", None, "zarya"):
        assert data.resolve(query).name == "ISS (ZARYA)", query

    result = data.next_pass("Tiangong", 24)
    assert result["status"] == "error"
    assert "CSS (TIANHE)" in result["error"] and "ISS (ZARYA)" in result["error"]
    assert result["spoken"].startswith("I can't say")


def test_an_empty_cache_says_so_rather_than_guessing(tmp_path):
    result = make_sky(tmp_path / "empty").next_pass("ISS", 48)
    assert result["status"] == "error"
    assert "empty" in result["error"] and "online" in result["error"]


def test_the_window_is_bounded(cache_dir):
    data = make_sky(cache_dir)
    assert data.next_pass("ISS", 0)["window_hours"] == 1
    assert data.next_pass("ISS", 10_000)["window_hours"] == sky.MAX_WINDOW_HOURS


# --- overhead now ------------------------------------------------------------------
def test_overhead_now_says_below_the_horizon_with_the_age(cache_dir):
    result = make_sky(cache_dir).overhead_now()

    assert result["status"] == "ok"
    assert result["overhead"] == []
    assert result["below_horizon"] == ["ISS (ZARYA)"]
    assert result["tle_age_hours"] == 12.0
    assert "below the horizon" in result["spoken"] and "elements" in result["spoken"]


def test_overhead_now_finds_the_station_at_culmination(cache_dir):
    data = make_sky(cache_dir)
    second = data.passes(data.resolve("ISS"), 48)[1]
    clock = FrozenClock(second.culmination)
    data = make_sky(cache_dir, clock=clock)

    result = data.overhead_now()
    assert len(result["overhead"]) == 1
    up = result["overhead"][0]
    assert up["satellite"] == "ISS (ZARYA)"
    assert abs(up["altitude"] - round(second.max_altitude)) <= 1
    assert up["direction"] == compass(second.culmination_azimuth)
    assert 400 <= up["range_km"] <= 2_500
    assert up["sunlit"] is second.sunlit
    assert "up now" in result["spoken"] and up["direction"] in result["spoken"]

    # Raise the bar above the pass and it is "not up".
    assert data.overhead_now(min_altitude=second.max_altitude + 5)["overhead"] == []


# --- the moon ------------------------------------------------------------------------
def test_the_moon_on_known_dates(cache_dir):
    data = make_sky(cache_dir)
    tonight = data.moon_phase()
    assert tonight["status"] == "ok"
    assert tonight["phase"] == "waxing gibbous" and tonight["waxing"] is True
    assert 97.0 < tonight["illumination"] < 99.0
    # Full 2026-08-28 04:18 UTC (05:18 BST); new 2026-09-11 03:27 UTC.
    assert tonight["next_full"].startswith("2026-08-28T05:18")
    assert tonight["next_new"].startswith("2026-09-11T04:2")
    assert "98 percent" in tonight["spoken"] and "full" in tonight["spoken"]

    eclipse = make_sky(cache_dir, clock=FrozenClock(datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)))
    new = eclipse.moon_phase()
    assert new["phase"] == "new moon" and new["illumination"] < 0.5

    full = make_sky(cache_dir, clock=FrozenClock(datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc)))
    assert full.moon_phase()["phase"] == "full moon"
    assert full.moon_phase()["illumination"] > 99.9


def test_moon_phase_names_by_angle():
    assert moon_phase_name(0) == "new moon"
    assert moon_phase_name(355) == "new moon"
    assert moon_phase_name(9) == "new moon"
    assert moon_phase_name(15) == "waxing crescent"
    assert moon_phase_name(90) == "first quarter"
    assert moon_phase_name(160) == "waxing gibbous"
    assert moon_phase_name(180) == "full moon"
    assert moon_phase_name(200) == "waning gibbous"
    assert moon_phase_name(270) == "last quarter"
    assert moon_phase_name(300) == "waning crescent"


def test_compass_words():
    assert compass(0) == "north"
    assert compass(44) == "north-east"
    assert compass(155) == "south-east"
    assert compass(180) == "south"
    assert compass(264.8) == "west"
    assert compass(357) == "north"
    assert compass(-90) == "west"


# --- the planets -----------------------------------------------------------------------
def test_planets_tonight_shape_and_a_known_night(cache_dir):
    result = make_sky(cache_dir).planets_tonight()

    assert result["status"] == "ok", result
    night = result["night"]
    assert night["already_dark"] is False
    assert night["dusk"].startswith("2026-08-26T20:35")
    assert night["dawn"].startswith("2026-08-27T05:29")

    names = [p["name"] for p in result["planets"]]
    assert set(names) <= {"Mercury", "Venus", "Mars", "Jupiter", "Saturn"}
    assert set(names) | set(result["not_up"]) == {"Mercury", "Venus", "Mars", "Jupiter", "Saturn"}
    for planet in result["planets"]:
        best = planet["best"]
        assert _dt(best["time"]) >= _dt(night["dusk"]) and _dt(best["time"]) <= _dt(night["dawn"])
        assert 0 <= best["altitude"] <= 90 and best["direction"] in sky.COMPASS
        assert isinstance(planet["up_at_dusk"], bool) and isinstance(planet["up_at_dawn"], bool)
        for key in ("rises", "sets"):
            assert planet[key] is None or _dt(planet[key])

    by_name = {p["name"]: p for p in result["planets"]}
    # Saturn, a month from opposition, is up almost all night and highest due south.
    saturn = by_name["Saturn"]
    assert 40 <= saturn["best"]["altitude"] <= 44 and saturn["best"]["direction"] == "south"
    assert saturn["rises"] and saturn["rises"].startswith("2026-08-26T21:1")
    assert saturn["up_at_dawn"] is True
    # Venus is an evening object, low in the west at dusk and gone within the hour.
    venus = by_name["Venus"]
    assert venus["up_at_dusk"] is True and venus["low"] is True and venus["sets"]
    assert "Mercury" in result["not_up"]

    spoken = result["spoken"]
    assert "20:35" in spoken and "05:29" in spoken
    assert "Saturn" in spoken and "south" in spoken and "Mercury" in spoken


def test_planets_tonight_when_it_is_already_dark(cache_dir):
    late = FrozenClock(NOW.replace(hour=23))
    result = make_sky(cache_dir, clock=late).planets_tonight()
    assert result["night"]["already_dark"] is True
    assert result["night"]["dusk"].startswith("2026-08-26T23:00")
    assert result["night"]["dawn"].startswith("2026-08-27T05:29")
    assert result["spoken"].startswith("It is already dark")


# --- without an ephemeris --------------------------------------------------------------
def test_without_an_ephemeris_passes_still_come_but_visibility_is_unknown(cache_dir, tmp_path):
    data = make_sky(cache_dir, ephemeris=tmp_path / "missing" / "de421.bsp")

    passes = data.next_pass("ISS", 48)
    assert passes["status"] == "ok" and passes["pass"] is not None
    assert passes["ephemeris"] is False
    assert passes["pass"]["visible"] is None and passes["pass"]["sunlit"] is None
    assert passes["next_visible"] is None
    assert "ephemeris" in passes["spoken"]

    moon = data.moon_phase()
    assert moon["status"] == "error" and "ephemeris" in moon["error"]
    assert "downloads are off" in moon["error"]
    planets = data.planets_tonight()
    assert planets["status"] == "error" and "ephemeris" in planets["error"]


# --- the element cache -----------------------------------------------------------------
async def test_a_stale_cache_keeps_serving_when_the_fetch_fails(tmp_path, caplog):
    clock = FrozenClock()
    directory = seed_cache(tmp_path / "tle", fetched_at=NOW - timedelta(hours=30))
    calls: list[str] = []

    async def offline(url: str) -> str:
        calls.append(url)
        raise ConnectionError("no route to host")

    cache = TleCache(directory, sources=[SOURCE], refresh_hours=24, download=True,
                     fetch=offline, now=clock)
    assert cache.stale_sources() == [SOURCE]

    with caplog.at_level(logging.WARNING, logger="jarvis.integrations.sky"):
        report = await cache.async_refresh()
        again = await cache.async_refresh()

    assert calls == [SOURCE, SOURCE]
    assert report["fetched"] == [] and len(report["failed"]) == 1
    assert "no route to host" in report["failed"][0]["error"]
    assert again["failed"] and again["fetched"] == []
    # The cache is still there, and it says how old it is.
    assert "ISS (ZARYA)" in cache.load()
    assert cache.age_hours() == 30.0
    assert cache.age_hours(cache.load()["ISS (ZARYA)"]) == 30.0
    # One warning for a source, however many times it fails.
    warnings = [r for r in caplog.records if "could not refresh" in r.getMessage()]
    assert len(warnings) == 1
    assert "30 h old" in warnings[0].getMessage()

    data = SkyData(LATITUDE, LONGITUDE, ELEVATION, cache=cache,
                   ephemeris=Ephemeris(EPHEMERIS, download=False), now=clock)
    result = data.next_pass("ISS", 48)
    assert result["status"] == "ok" and result["tle_age_hours"] == 30.0
    assert "1 day old" in result["spoken"]


async def test_a_fresh_cache_is_not_fetched(cache_dir):
    calls: list[str] = []

    async def spy(url: str) -> str:
        calls.append(url)
        return OMM.read_text()

    cache = TleCache(cache_dir, sources=[SOURCE], refresh_hours=24, download=True,
                     fetch=spy, now=FrozenClock())
    assert cache.stale_sources() == []
    report = await cache.async_refresh()
    assert calls == []
    assert report["kept"] == [SOURCE] and report["fetched"] == []


async def test_a_successful_fetch_replaces_the_set_and_its_clock(tmp_path):
    clock = FrozenClock()
    directory = seed_cache(tmp_path / "tle", fetched_at=NOW - timedelta(days=3))
    # CelesTrak serves CRLF; the reader must not care.
    body = OMM.read_text().replace("\n", "\r\n")

    async def online(url: str) -> str:
        return body

    cache = TleCache(directory, sources=[SOURCE], refresh_hours=24, download=True,
                     fetch=online, now=clock)
    assert cache.age_hours() == 72.0
    report = await cache.async_refresh()
    assert len(report["fetched"]) == 1 and report["fetched"][0]["satellites"] == 1
    assert report["fetched"][0]["file"] == "stations.csv"
    assert cache.age_hours() == 0.0
    meta = json.loads((directory / "stations.meta.json").read_text())
    assert meta["fetched_at"] == NOW.isoformat() and meta["url"] == SOURCE
    assert "ISS (ZARYA)" in cache.load()
    assert not list(directory.glob("*.part"))


async def test_a_body_with_no_satellites_cannot_clobber_a_good_cache(tmp_path):
    directory = seed_cache(tmp_path / "tle", fetched_at=NOW - timedelta(days=3))
    before = (directory / "stations.csv").read_text()

    async def error_page(url: str) -> str:
        return "<html><body>403 Forbidden</body></html>"

    cache = TleCache(directory, sources=[SOURCE], download=True, fetch=error_page,
                     now=FrozenClock())
    report = await cache.async_refresh()
    assert report["fetched"] == [] and "no satellites" in report["failed"][0]["error"]
    assert (directory / "stations.csv").read_text() == before
    assert cache.age_hours() == 72.0


async def test_download_off_means_no_fetch_however_stale(tmp_path):
    directory = seed_cache(tmp_path / "tle", fetched_at=NOW - timedelta(days=40))
    calls: list[str] = []

    async def spy(url: str) -> str:
        calls.append(url)
        return OMM.read_text()

    cache = TleCache(directory, sources=[SOURCE], download=False, fetch=spy, now=FrozenClock())
    report = await cache.async_refresh(force=True)
    assert calls == [] and report["disabled"] is True
    assert cache.age_hours() == 40 * 24.0


def test_refresh_never_goes_below_celestraks_cycle(tmp_path):
    assert TleCache(tmp_path, sources=[SOURCE], refresh_hours=0.5).refresh_hours == MIN_REFRESH_HOURS
    assert TleCache(tmp_path, sources=[SOURCE], refresh_hours=12).refresh_hours == 12.0


def test_a_tle_dropped_in_by_hand_is_read_and_dated_by_its_mtime(tmp_path):
    """The fallback: no meta file, no CSV — a TLE somebody typed in, dated by mtime."""
    directory = tmp_path / "tle"
    directory.mkdir()
    shutil.copy(TLE, directory / "mine.tle")
    cache = TleCache(directory, sources=[], now=lambda: datetime.now(timezone.utc))
    assert "ISS (ZARYA)" in cache.load()
    age = cache.age_hours()
    assert age is not None and age < 1.0


def test_parse_elements_reads_omm_csv_and_falls_back_to_tle():
    """One element set, two encodings, one orbit.

    CSV is what is fetched (a six-digit catalogue number has no TLE form);
    the TLE reader is for a hand-typed file. Both must build the same
    satellite: same epoch to the second, same position to the kilometre.
    """
    from_csv = parse_elements(OMM.read_text())
    from_tle = parse_elements(TLE.read_text())
    assert [e.name for e in from_csv] == ["ISS (ZARYA)"]
    assert [e.name for e in from_tle] == ["ISS (ZARYA)"]
    assert from_csv[0].omm is not None and from_tle[0].lines is not None

    ts = sky._sf()["ts"]
    a, b = from_tle[0].build(ts), from_csv[0].build(ts)
    t = ts.from_datetime(NOW.astimezone(timezone.utc))
    assert abs(a.epoch.tt - b.epoch.tt) * 86400 < 1.0
    distance = ((a.at(t).position.km - b.at(t).position.km) ** 2).sum() ** 0.5
    assert distance < 1.0, f"the two readings of one element set are {distance:.2f} km apart"

    assert parse_elements("") == []
    assert parse_elements("<html>nothing</html>") == []
    # A two-line set with no name line still parses, under its catalogue number.
    nameless = "\n".join(TLE.read_text().splitlines()[1:])
    assert [e.name for e in parse_elements(nameless)] == ["NORAD 25544"]


# --- through setup: entities and tools ------------------------------------------------
@pytest.fixture
async def jarvis(tmp_path):
    instance = Jarvis(tmp_path)
    instance.config = {
        "jarvis": {
            "latitude": LATITUDE, "longitude": LONGITUDE, "elevation": ELEVATION,
            "time_zone": "Europe/London",
        }
    }
    instance.data["llm_tools"] = ToolRegistry(instance)
    instance.data[DATA_CLOCK] = FrozenClock()
    await instance.async_start()
    yield instance
    await instance.async_stop()


async def test_setup_registers_entities_and_read_only_tools(jarvis, tmp_path):
    seed_cache(tmp_path / "sky" / "tle")
    assert await async_setup(jarvis, {
        "download": False,
        "tle_cache": "sky/tle",
        "ephemeris": str(EPHEMERIS),
    }) is True

    # The entities, computed before they were added — no "unknown" window.
    state = jarvis.states.get("sky.iss_next_pass")
    assert state is not None and state.state.startswith("2026-08-27T01:35")
    assert state.attributes["max_alt"] == 11 and state.attributes["visible"] is False
    assert state.attributes["direction"] == "south-east"
    assert state.attributes["tle_age_hours"] == 12.0
    assert state.attributes["next_visible"].startswith("2026-08-27T04:45")
    moon = jarvis.states.get("sky.moon")
    assert moon is not None and moon.state == "waxing gibbous"
    assert 97.0 < moon.attributes["illumination"] < 99.0
    assert moon.attributes["next_full"].startswith("2026-08-28T05:18")

    registry = jarvis.data["llm_tools"]
    for name in ("next_pass", "overhead_now", "moon_phase", "planets_tonight"):
        tool = registry.get(name)
        assert tool is not None, name
        assert tool.tier == TIER_DIRECT and tool.read_only, name

    passes = await registry.get("next_pass").handler({"satellite": "space station", "hours": 48})
    assert passes["status"] == "ok" and "04:45" in passes["spoken"]
    assert passes["computed_in_ms"] >= 0
    overhead = await registry.get("overhead_now").handler({"min_altitude": "10"})
    assert overhead["status"] == "ok" and overhead["below_horizon"] == ["ISS (ZARYA)"]
    moon = await registry.get("moon_phase").handler({})
    assert moon["phase"] == "waxing gibbous"
    planets = await registry.get("planets_tonight").handler({})
    assert planets["status"] == "ok" and "Saturn" in planets["spoken"]
    # Bad arguments degrade to the defaults rather than a traceback.
    assert (await registry.get("next_pass").handler({"hours": "soon"}))["window_hours"] == 48

    # Downloads off: nothing was ever refreshed and no loop was started.
    assert jarvis.data["sky"].cache.last_refresh == {}


async def test_setup_with_downloads_on_never_blocks_and_a_failed_fetch_keeps_the_cache(
    jarvis, tmp_path, caplog
):
    """`download: true` on a box that is offline (here: a client that refuses).

    Setup returns at once; the refresh runs afterwards, fails, keeps the
    fixture set, and the entities were computed from it all the same.
    """
    seed_cache(tmp_path / "sky" / "tle", fetched_at=NOW - timedelta(days=2))
    with caplog.at_level(logging.WARNING, logger="jarvis.integrations.sky"):
        assert await async_setup(jarvis, {
            "tle_cache": "sky/tle",
            "ephemeris": str(EPHEMERIS),
            "refresh_hours": 24,
        }) is True
        # Let the warm-up task run its fetch (and fail) without waiting an hour.
        for _ in range(20):
            await asyncio.sleep(0)
            if jarvis.data["sky"].cache.last_refresh:
                break

    cache = jarvis.data["sky"].cache
    assert cache.last_refresh["fetched"] == []
    assert len(cache.last_refresh["failed"]) == 1
    assert "must not open a network client" in cache.last_refresh["failed"][0]["error"]
    assert any("could not refresh" in r.getMessage() for r in caplog.records)
    assert cache.age_hours() == 48.0
    state = jarvis.states.get("sky.iss_next_pass")
    assert state is not None and state.state.startswith("2026-08-27T01:35")
    assert state.attributes["tle_age_hours"] == 48.0
    # The ephemeris was already on disk, so nothing tried to download it.
    assert jarvis.data["sky"].ephemeris.last_error == ""


async def test_setup_without_coordinates_still_works_and_warns(tmp_path, caplog):
    instance = Jarvis(tmp_path)
    instance.config = {"jarvis": {}}
    instance.data[DATA_CLOCK] = FrozenClock()
    await instance.async_start()
    try:
        seed_cache(tmp_path / "sky" / "tle")
        with caplog.at_level(logging.WARNING, logger="jarvis.integrations.sky"):
            assert await async_setup(instance, {"download": False, "ephemeris": str(EPHEMERIS)}) is True
        assert any("no latitude/longitude" in r.getMessage() for r in caplog.records)
        assert instance.states.get("sky.iss_next_pass") is not None
        assert instance.states.get("sky.moon").state == "waxing gibbous"
    finally:
        await instance.async_stop()
