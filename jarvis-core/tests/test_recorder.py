"""Recorder, history, logbook, sun and person.

Everything here runs against a temporary SQLite file and the real event
bus — no network, no broker, no hardware.
"""

import asyncio
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.const import STATE_HOME, STATE_NOT_HOME, STATE_OFF, STATE_ON  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import history as history_integration  # noqa: E402
from jarvis.integrations import logbook as logbook_integration  # noqa: E402
from jarvis.integrations import person as person_integration  # noqa: E402
from jarvis.integrations import recorder as recorder_integration  # noqa: E402
from jarvis.integrations import sun as sun_integration  # noqa: E402
from jarvis.integrations.sun import solar  # noqa: E402

DAY = 86400.0


# --- fixtures --------------------------------------------------------------
@pytest.fixture
async def jarvis(tmp_path):
    """A bare, running Jarvis with no integrations set up.

    Started up front so teardown runs the real shutdown path (which flushes
    and closes the recorder's database).
    """
    instance = Jarvis(tmp_path)
    instance.config = {"jarvis": {"latitude": 40.71, "longitude": -74.01}}
    await instance.async_start()
    yield instance
    await instance.async_stop()


async def _setup_recorder(jarvis, **options):
    options.setdefault("db_file", "test.db")
    options.setdefault("commit_interval", 0)  # flush only when we ask
    options.setdefault("auto_purge", False)
    assert await recorder_integration.async_setup(jarvis, options) is True
    return jarvis.data["recorder"]


# --- recorder --------------------------------------------------------------
async def test_recorder_writes_and_reads_back_states(jarvis, tmp_path):
    recorder = await _setup_recorder(jarvis)
    assert Path(recorder.db_path) == tmp_path / "test.db"

    jarvis.states.set("light.kitchen", STATE_ON, {"brightness": 180})
    jarvis.states.set("light.kitchen", STATE_OFF)
    await recorder.async_commit()

    rows = await recorder.states_between(["light.kitchen"])
    assert [row["state"] for row in rows] == [STATE_ON, STATE_OFF]
    assert rows[0]["attributes"]["brightness"] == 180
    assert rows[0]["entity_id"] == "light.kitchen"
    # Timestamps come back usable in both shapes.
    assert isinstance(rows[0]["last_updated"], float)
    assert rows[0]["last_updated_iso"].endswith("+00:00")
    assert rows[0]["last_updated"] <= rows[1]["last_updated"]

    # The file really is on disk.
    assert (tmp_path / "test.db").exists()


async def test_recorder_records_events_but_not_state_changed(jarvis):
    recorder = await _setup_recorder(jarvis)

    jarvis.states.set("light.kitchen", STATE_ON)
    jarvis.bus.fire("custom_thing", {"answer": 42})
    await recorder.async_commit()

    events = await recorder.events_between()
    types = [event["event_type"] for event in events]
    assert "custom_thing" in types
    assert "state_changed" not in types  # those live in `states`
    custom = next(e for e in events if e["event_type"] == "custom_thing")
    assert custom["data"]["answer"] == 42

    filtered = await recorder.events_between(event_types=["custom_thing"])
    assert len(filtered) == 1


async def test_recorder_time_window_filters_rows(jarvis):
    recorder = await _setup_recorder(jarvis)
    jarvis.states.set("sensor.probe", "1")
    await recorder.async_commit()

    now = time.time()
    assert await recorder.states_between(["sensor.probe"], now - 60, now + 60)
    assert not await recorder.states_between(["sensor.probe"], now + 10, now + 60)

    for value in ("2", "3", "4"):
        jarvis.states.set("sensor.probe", value)
    assert len(await recorder.states_between(["sensor.probe"], limit=2)) == 2
    assert await recorder.recorded_entity_ids() == ["sensor.probe"]


async def test_recorder_exclude_filters(jarvis):
    recorder = await _setup_recorder(
        jarvis,
        exclude={
            "domains": ["sensor"],
            "entities": ["light.noisy"],
            "entity_globs": ["switch.*_debug"],
        },
    )

    jarvis.states.set("sensor.noise", "1")
    jarvis.states.set("light.noisy", STATE_ON)
    jarvis.states.set("switch.rack_debug", STATE_ON)
    jarvis.states.set("light.kept", STATE_ON)
    await recorder.async_commit()

    recorded = {row["entity_id"] for row in await recorder.states_between()}
    assert recorded == {"light.kept"}


async def test_recorder_include_filters(jarvis):
    recorder = await _setup_recorder(
        jarvis, include={"domains": ["light"], "entities": ["sensor.outside"]}
    )

    jarvis.states.set("light.hall", STATE_ON)
    jarvis.states.set("sensor.outside", "12.5")
    jarvis.states.set("sensor.inside", "21.0")
    jarvis.states.set("switch.pump", STATE_ON)
    await recorder.async_commit()

    recorded = {row["entity_id"] for row in await recorder.states_between()}
    assert recorded == {"light.hall", "sensor.outside"}


async def test_entity_filter_precedence():
    entity_filter = recorder_integration.EntityFilter(
        include={"entities": ["sensor.keep_me"]},
        exclude={"domains": ["sensor"]},
    )
    # An explicit include beats a domain-wide exclude.
    assert entity_filter("sensor.keep_me") is True
    assert entity_filter("sensor.other") is False
    # An include block that names only entities excludes everything else.
    assert entity_filter("light.hall") is False


async def test_recorder_purge_drops_old_keeps_recent(jarvis):
    recorder = await _setup_recorder(jarvis)
    jarvis.states.set("light.kitchen", STATE_ON)
    await recorder.async_commit()

    # Backdate one row by 30 days, leave the fresh one alone.
    old = time.time() - 30 * DAY
    await recorder._execute(
        "INSERT INTO states (entity_id, state, attributes, last_changed, last_updated) "
        "VALUES (?, ?, ?, ?, ?)",
        ("light.kitchen", STATE_OFF, "{}", old, old),
    )
    await recorder._execute(
        "INSERT INTO events (event_type, data, time_fired) VALUES (?, ?, ?)",
        ("ancient", "{}", old),
    )
    assert (await recorder.row_counts())["states"] == 2

    removed = await recorder.purge(keep_days=10)
    assert removed == 2  # one old state + one old event

    counts = await recorder.row_counts()
    assert counts["states"] == 1
    assert counts["events"] == 0
    remaining = await recorder.states_between(["light.kitchen"])
    assert [row["state"] for row in remaining] == [STATE_ON]


async def test_recorder_purge_services(jarvis):
    recorder = await _setup_recorder(jarvis)
    jarvis.states.set("light.kitchen", STATE_ON)
    jarvis.states.set("sensor.temp", "20")
    await recorder.async_commit()

    assert jarvis.services.has_service("recorder", "purge")
    result = await jarvis.async_call_service(
        "recorder", "purge_entities", {"domains": ["sensor"]}, return_response=True
    )
    assert result["removed"] == 1
    assert {row["entity_id"] for row in await recorder.states_between()} == {
        "light.kitchen"
    }

    result = await jarvis.async_call_service(
        "recorder", "purge", {"keep_days": 0}, return_response=True
    )
    assert result["removed"] >= 1
    assert (await recorder.row_counts())["states"] == 0


async def test_recorder_commit_loop_flushes_without_being_asked(jarvis):
    recorder = await _setup_recorder(jarvis, commit_interval=0.05)

    jarvis.states.set("light.kitchen", STATE_ON)
    assert recorder._state_queue, "the write should be queued, not written inline"

    await asyncio.sleep(0.2)
    assert not recorder._state_queue, "the commit loop should have drained the queue"

    # Read straight from SQLite so the query API cannot flush on our behalf.
    rows = await recorder._execute("SELECT state FROM states")
    assert [row["state"] for row in rows] == [STATE_ON]


async def test_recorder_shutdown_flushes_pending_writes(jarvis, tmp_path):
    recorder = await _setup_recorder(jarvis, commit_interval=60)
    jarvis.states.set("light.kitchen", STATE_ON)
    assert recorder._state_queue

    await jarvis.async_stop()  # the fixture's second stop is a no-op

    import sqlite3

    with sqlite3.connect(tmp_path / "test.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM states").fetchone()[0] == 1


async def test_nightly_purge_is_scheduled_for_the_next_0412():
    at_noon = datetime(2024, 6, 21, 12, 0, 0)
    seconds = recorder_integration._seconds_until_next_purge(at_noon)
    assert seconds == pytest.approx((16 * 60 + 12) * 60)  # 04:12 tomorrow

    just_before = datetime(2024, 6, 21, 4, 0, 0)
    assert recorder_integration._seconds_until_next_purge(just_before) == pytest.approx(
        12 * 60
    )


async def test_recorder_db_url_and_timestamp_parsing(jarvis, tmp_path):
    recorder = recorder_integration.Recorder(
        jarvis, {"db_url": f"sqlite:///{tmp_path / 'via_url.db'}"}
    )
    assert recorder.db_path == str(tmp_path / "via_url.db")

    moment = datetime(2024, 6, 21, 12, 0, tzinfo=timezone.utc)
    epoch = moment.timestamp()
    assert recorder_integration.as_timestamp(moment) == epoch
    assert recorder_integration.as_timestamp("2024-06-21T12:00:00+00:00") == epoch
    assert recorder_integration.as_timestamp(datetime(2024, 6, 21, 12, 0)) == epoch
    assert recorder_integration.as_timestamp(epoch) == epoch
    assert recorder_integration.as_timestamp(None, 7.0) == 7.0


# --- history ---------------------------------------------------------------
async def test_history_returns_rows_for_entity(jarvis):
    await _setup_recorder(jarvis)
    assert await history_integration.async_setup(jarvis, {}) is True

    jarvis.states.set("light.kitchen", STATE_ON)
    jarvis.states.set("light.kitchen", STATE_OFF)
    jarvis.states.set("light.hall", STATE_ON)

    history = await history_integration.get_history(jarvis, ["light.kitchen"])
    assert list(history) == ["light.kitchen"]
    assert [row["state"] for row in history["light.kitchen"]] == [STATE_ON, STATE_OFF]

    both = await history_integration.get_history(
        jarvis, ["light.kitchen", "light.hall"]
    )
    assert len(both["light.hall"]) == 1


async def test_history_includes_state_at_window_start(jarvis):
    recorder = await _setup_recorder(jarvis)
    await history_integration.async_setup(jarvis, {})

    jarvis.states.set("light.kitchen", STATE_ON)  # before the window
    await recorder.async_commit()
    await asyncio.sleep(0.02)
    boundary = time.time()  # strictly between the two changes
    await asyncio.sleep(0.02)
    jarvis.states.set("light.kitchen", STATE_OFF)  # inside the window

    history = await history_integration.get_history(
        jarvis, ["light.kitchen"], start=boundary
    )
    states = [row["state"] for row in history["light.kitchen"]]
    assert states == [STATE_ON, STATE_OFF], "series must open with the prior state"

    without = await history_integration.get_history(
        jarvis, ["light.kitchen"], start=boundary, include_start_time_state=False
    )
    assert [row["state"] for row in without["light.kitchen"]] == [STATE_OFF]


async def test_history_get_service_and_stats(jarvis):
    await _setup_recorder(jarvis)
    await history_integration.async_setup(jarvis, {})

    for value in ("18.0", "20.0", "22.0"):
        jarvis.states.set("sensor.temperature", value)

    response = await jarvis.async_call_service(
        "history", "get", {"entity_id": "sensor.temperature"}, return_response=True
    )
    series = response["history"]["sensor.temperature"]
    assert [row["state"] for row in series] == ["18.0", "20.0", "22.0"]

    stats = await jarvis.async_call_service(
        "history", "stats", {"entity_id": "sensor.temperature"}, return_response=True
    )
    summary = stats["stats"]["sensor.temperature"]
    assert summary["min"] == 18.0
    assert summary["max"] == 22.0
    assert summary["mean"] == 20.0
    assert summary["changes"] == 2


async def test_history_without_recorder_falls_back_to_current_state(jarvis):
    await history_integration.async_setup(jarvis, {})
    jarvis.states.set("light.kitchen", STATE_ON)

    history = await history_integration.get_history(jarvis, ["light.kitchen"])
    assert [row["state"] for row in history["light.kitchen"]] == [STATE_ON]


# --- logbook ---------------------------------------------------------------
async def test_logbook_log_stores_an_entry(jarvis):
    assert await logbook_integration.async_setup(jarvis, {}) is True

    result = await jarvis.async_call_service(
        "logbook",
        "log",
        {"name": "Dishwasher", "message": "finished its cycle"},
        return_response=True,
    )
    assert result["logged"] is True

    response = await jarvis.async_call_service("logbook", "get", {}, return_response=True)
    entries = response["entries"]
    assert any(
        e["name"] == "Dishwasher" and e["message"] == "finished its cycle"
        for e in entries
    )
    entry = next(e for e in entries if e["name"] == "Dishwasher")
    assert entry["source"] == "user"
    assert entry["when_iso"].endswith("+00:00")


async def test_logbook_describes_state_changes(jarvis):
    await logbook_integration.async_setup(jarvis, {"log_service_calls": False})
    logbook = jarvis.data["logbook"]

    jarvis.states.set("light.kitchen", STATE_OFF, {"friendly_name": "Kitchen Light"})
    jarvis.states.set("light.kitchen", STATE_ON, {"friendly_name": "Kitchen Light"})
    jarvis.states.set(
        "binary_sensor.front_door",
        STATE_OFF,
        {"friendly_name": "Front Door", "device_class": "door"},
    )
    jarvis.states.set(
        "binary_sensor.front_door",
        STATE_ON,
        {"friendly_name": "Front Door", "device_class": "door"},
    )
    # Continuous sensors stay out of the feed.
    jarvis.states.set("sensor.temperature", "21", {"unit_of_measurement": "°C"})
    jarvis.states.set("sensor.temperature", "22", {"unit_of_measurement": "°C"})

    messages = [(e["name"], e["message"]) for e in logbook.entries]
    assert ("Kitchen Light", "turned on") in messages
    assert ("Front Door", "was opened") in messages
    assert not any(name == "Temperature" for name, _ in messages)


async def test_logbook_filters_by_entity_and_window(jarvis):
    await logbook_integration.async_setup(jarvis, {"log_service_calls": False})
    logbook = jarvis.data["logbook"]

    jarvis.states.set("light.a", STATE_OFF)
    jarvis.states.set("light.a", STATE_ON)
    jarvis.states.set("light.b", STATE_OFF)
    jarvis.states.set("light.b", STATE_ON)

    only_a = await logbook.async_get(entity_ids=["light.a"])
    assert only_a and all(e["entity_id"] == "light.a" for e in only_a)

    future = await logbook.async_get(start=time.time() + 60, end=time.time() + 120)
    assert future == []


async def test_logbook_survives_restart_via_recorder(jarvis):
    """Entries written before a restart come back from the database."""
    recorder = await _setup_recorder(jarvis)
    await logbook_integration.async_setup(jarvis, {})
    logbook = jarvis.data["logbook"]

    await logbook.async_log("left the garage open", name="Garage")
    await recorder.async_commit()

    # Simulate a restart: the in-memory buffer is gone, the database is not.
    logbook.entries.clear()
    entries = await logbook.async_get()
    assert any(e["message"] == "left the garage open" for e in entries)


async def test_logbook_does_not_duplicate_live_and_recorded_entries(jarvis):
    """The buffer and the database describe the same change — report it once."""
    await _setup_recorder(jarvis)
    await logbook_integration.async_setup(jarvis, {"log_service_calls": False})
    logbook = jarvis.data["logbook"]

    jarvis.states.set("light.kitchen", STATE_OFF, {"friendly_name": "Kitchen Light"})
    jarvis.states.set("light.kitchen", STATE_ON, {"friendly_name": "Kitchen Light"})
    await logbook.async_log("was reset", name="Kitchen Light")

    entries = await logbook.async_get()
    turned_on = [e for e in entries if e["message"] == "turned on"]
    reset = [e for e in entries if e["message"] == "was reset"]
    assert len(turned_on) == 1
    assert len(reset) == 1


async def test_logbook_records_service_calls(jarvis):
    await logbook_integration.async_setup(jarvis, {"log_service_calls": True})
    logbook = jarvis.data["logbook"]

    jarvis.services.register("light", "turn_on", lambda call: None)
    await jarvis.async_call_service("light", "turn_on", {"entity_id": "light.kitchen"})

    assert any(
        e["source"] == "service" and "light.turn_on" in e["message"]
        for e in logbook.entries
    )
    # Its own bookkeeping calls stay out of the feed.
    await jarvis.async_call_service("logbook", "get", {}, return_response=True)
    assert not any("logbook.get" in e["message"] for e in logbook.entries)


# --- sun -------------------------------------------------------------------
async def test_sun_computes_plausible_sunrise_and_sunset():
    # New York, summer solstice: sunrise ~09:25 UTC, sunset ~00:31 UTC (+1d).
    latitude, longitude = 40.0, -74.0
    sunrise, sunset = solar.sun_times(latitude, longitude, date(2024, 6, 21))

    assert sunrise is not None and sunset is not None
    assert sunrise < sunset, "the sun must rise before it sets"

    day_length = (sunset - sunrise).total_seconds() / 3600.0
    assert 14.0 < day_length < 16.0, f"solstice day length was {day_length}h"

    # Sunrise is in the morning local time (UTC-4 in June).
    assert 8.0 < sunrise.hour + sunrise.minute / 60 < 11.0

    # Winter days are shorter than summer days at this latitude.
    winter_rise, winter_set = solar.sun_times(latitude, longitude, date(2024, 12, 21))
    winter_length = (winter_set - winter_rise).total_seconds() / 3600.0
    assert 8.0 < winter_length < 10.0
    assert winter_length < day_length


async def test_sun_position_at_local_noon_is_above_horizon():
    latitude, longitude = 40.0, -74.0
    # Solar noon at 74°W is ~16:56 UTC.
    noon = datetime(2024, 6, 21, 17, 0, tzinfo=timezone.utc)
    elevation, azimuth = solar.solar_position(latitude, longitude, noon)

    assert -90.0 <= elevation <= 90.0
    assert 0.0 <= azimuth <= 360.0
    assert elevation > 60.0, "midsummer noon sun should be high"
    assert solar.is_up(latitude, longitude, noon) is True

    midnight = datetime(2024, 6, 21, 5, 0, tzinfo=timezone.utc)
    night_elevation, _ = solar.solar_position(latitude, longitude, midnight)
    assert -90.0 <= night_elevation <= 90.0
    assert night_elevation < 0.0
    assert solar.is_up(latitude, longitude, midnight) is False


async def test_sun_handles_polar_day():
    # Svalbard in June: the sun never sets.
    assert solar.sun_times(78.0, 15.0, date(2024, 6, 21)) == (None, None)
    assert solar.is_up(78.0, 15.0, datetime(2024, 6, 21, 2, 0, tzinfo=timezone.utc))
    # It does come back eventually.
    resumes = solar.next_event(
        78.0, 15.0, "sunrise", datetime(2024, 6, 21, tzinfo=timezone.utc)
    )
    assert resumes is not None and resumes.month == 8


async def test_sun_entity_and_helpers(jarvis):
    assert await sun_integration.async_setup(jarvis, {}) is True

    state = jarvis.states.get("sun.sun")
    assert state is not None
    assert state.state in ("above_horizon", "below_horizon")
    for key in ("next_rising", "next_setting", "elevation", "azimuth", "rising"):
        assert key in state.attributes
    assert -90.0 <= state.attributes["elevation"] <= 90.0
    assert state.attributes["next_rising"] > datetime.now(timezone.utc).isoformat()

    data = sun_integration.get_sun(jarvis)
    assert data.latitude == 40.71

    noon = datetime(2024, 6, 21, 17, 0, tzinfo=timezone.utc)
    assert data.is_up(noon) is True
    assert sun_integration.is_up(jarvis, noon) is True

    # An offset trigger ("30 minutes before sunset") lands before the event.
    sunset = data.next("sunset", noon)
    early = sun_integration.next_event_at(
        jarvis, "sunset", noon, offset=timedelta(minutes=-30)
    )
    assert early is not None and early < sunset
    assert early > noon


async def test_sun_entity_state_matches_computed_position(jarvis):
    await sun_integration.async_setup(jarvis, {})
    entity = jarvis.entity_object("sun.sun")

    snapshot = entity.recompute(datetime(2024, 6, 21, 17, 0, tzinfo=timezone.utc))
    assert entity.state == "above_horizon"
    assert snapshot["elevation"] > 60.0
    assert snapshot["rising"] is False  # just past solar noon

    entity.recompute(datetime(2024, 6, 21, 5, 0, tzinfo=timezone.utc))
    assert entity.state == "below_horizon"


# --- person ----------------------------------------------------------------
async def test_person_state_follows_device_tracker(jarvis):
    assert (
        await person_integration.async_setup(
            jarvis,
            [{"name": "Chris", "device_trackers": ["device_tracker.chris_phone"]}],
        )
        is True
    )

    state = jarvis.states.get("person.chris")
    assert state is not None
    assert state.state == "unknown"
    assert state.attributes["device_trackers"] == ["device_tracker.chris_phone"]

    jarvis.states.set("device_tracker.chris_phone", STATE_HOME)
    person = jarvis.states.get("person.chris")
    assert person.state == STATE_HOME
    assert person.attributes["source"] == "device_tracker.chris_phone"

    jarvis.states.set("device_tracker.chris_phone", STATE_NOT_HOME)
    assert jarvis.states.get("person.chris").state == STATE_NOT_HOME

    # A named place passes straight through.
    jarvis.states.set("device_tracker.chris_phone", "Office")
    assert jarvis.states.get("person.chris").state == "Office"


async def test_person_prefers_a_tracker_that_is_home(jarvis):
    await person_integration.async_setup(
        jarvis,
        [
            {
                "name": "Sam",
                "device_trackers": [
                    "device_tracker.sam_phone",
                    "device_tracker.sam_watch",
                ],
            }
        ],
    )

    jarvis.states.set("device_tracker.sam_phone", STATE_NOT_HOME)
    jarvis.states.set("device_tracker.sam_watch", STATE_HOME)
    assert jarvis.states.get("person.sam").state == STATE_HOME

    jarvis.states.set("device_tracker.sam_watch", STATE_NOT_HOME)
    assert jarvis.states.get("person.sam").state == STATE_NOT_HOME


async def test_device_tracker_see_service(jarvis):
    await person_integration.async_setup(
        jarvis, [{"name": "Chris", "device_trackers": ["device_tracker.chris_phone"]}]
    )
    assert jarvis.services.has_service("device_tracker", "see")

    # GPS at the configured home coordinates → home.
    result = await jarvis.async_call_service(
        "device_tracker",
        "see",
        {"dev_id": "chris_phone", "gps": [40.71, -74.01], "battery": 84},
        return_response=True,
    )
    assert result == {
        "seen": True,
        "entity_id": "device_tracker.chris_phone",
        "state": STATE_HOME,
    }
    tracker = jarvis.states.get("device_tracker.chris_phone")
    assert tracker.attributes["latitude"] == 40.71
    assert tracker.attributes["battery_level"] == 84
    assert jarvis.states.get("person.chris").state == STATE_HOME

    # A few kilometres away → not_home.
    await jarvis.async_call_service(
        "device_tracker", "see", {"dev_id": "chris_phone", "gps": [40.85, -74.01]}
    )
    assert jarvis.states.get("device_tracker.chris_phone").state == STATE_NOT_HOME
    assert jarvis.states.get("person.chris").state == STATE_NOT_HOME

    # An explicit location name wins over GPS.
    await jarvis.async_call_service(
        "device_tracker",
        "see",
        {"dev_id": "chris_phone", "location_name": "Gym", "gps": [40.71, -74.01]},
    )
    assert jarvis.states.get("person.chris").state == "Gym"


async def test_device_tracker_see_requires_an_id(jarvis):
    await person_integration.async_setup(jarvis, [])
    result = await jarvis.async_call_service(
        "device_tracker", "see", {"gps": [1.0, 2.0]}, return_response=True
    )
    assert result["seen"] is False


# --- wiring ----------------------------------------------------------------
async def test_full_stack_through_async_setup(tmp_path):
    """The loader wires recorder → history → logbook → sun → person."""
    instance = Jarvis(tmp_path)
    config = {
        "jarvis": {"latitude": 40.71, "longitude": -74.01},
        "history": {},
        "logbook": {},
        "sun": {},
        "person": [{"name": "Chris", "device_trackers": ["device_tracker.chris_phone"]}],
        "recorder": {"db_file": "wired.db", "commit_interval": 0, "auto_purge": False},
    }
    try:
        await instance.async_setup(config)

        # `history` depends on `recorder`, so the loader must have set it up.
        assert "recorder" in instance.data
        assert instance.services.has_service("history", "get")
        assert instance.services.has_service("logbook", "log")
        assert instance.states.get("sun.sun") is not None
        assert instance.states.get("person.chris") is not None

        instance.states.set("light.kitchen", STATE_ON)
        history = await history_integration.get_history(instance, ["light.kitchen"])
        assert [row["state"] for row in history["light.kitchen"]] == [STATE_ON]
    finally:
        await instance.async_stop()

    assert (tmp_path / "wired.db").exists()


# --- regression tests (verify pass) ----------------------------------------
# Each test below pins a defect found while auditing this subsystem. They
# fail against the original implementation.


async def test_commit_loop_survives_a_write_failure_and_keeps_the_rows(jarvis):
    """A transient sqlite error must not kill recording for good.

    Regression: the flush loop only re-raised CancelledError, so any other
    exception ended the task permanently — and async_commit had already
    emptied the queues, so the rows it was carrying vanished too. The
    recorder went silently write-only-to-nowhere for the rest of the run.
    """
    import sqlite3

    recorder = await _setup_recorder(jarvis, commit_interval=0.05)

    calls = {"n": 0}
    real_write = recorder._write_sync

    def flaky(states, events):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_write(states, events)

    recorder._write_sync = flaky

    jarvis.states.set("light.first", STATE_ON)
    await asyncio.sleep(0.25)

    assert calls["n"] >= 2, "the loop must retry after a failed write"
    assert not recorder._commit_task.done(), "the flush loop must still be alive"

    jarvis.states.set("light.second", STATE_ON)
    await asyncio.sleep(0.25)

    rows = await recorder._execute("SELECT entity_id FROM states")
    recorded = {row["entity_id"] for row in rows}
    assert recorded == {"light.first", "light.second"}, (
        "the row queued when the write failed must survive the retry"
    )


async def test_shutdown_waits_for_an_in_flight_write(jarvis, tmp_path):
    """Closing the database must not yank it away from a running flush.

    Regression: async_shutdown cancelled the flush task without awaiting it
    and then closed the connection off the lock, so a write already running
    on a worker thread re-read `self._conn`, found None, and dropped its
    rows without a sound.
    """
    import sqlite3
    import threading

    recorder = await _setup_recorder(jarvis, commit_interval=0.05)

    entered = threading.Event()
    trace: list[str] = []
    real_write = recorder._write_sync
    real_close = recorder._close

    def slow(states, events):
        # Only the first flush is slow. Later ones (the jarvis_stop event
        # that async_stop queues) must not accidentally hold the close back
        # and paper over the race this test is about.
        first = not trace
        trace.append("write-start")
        entered.set()
        if first:
            time.sleep(0.3)
        written = real_write(states, events)
        trace.append(f"write-end:{written}")
        return written

    def traced_close():
        trace.append("close")
        real_close()

    recorder._write_sync = slow
    recorder._close = traced_close

    jarvis.states.set("light.inflight", STATE_ON)
    for _ in range(200):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set(), "the flush should have started"

    await jarvis.async_stop()

    # Ordering, not just the end state: the close must come after every
    # write has finished, otherwise this only ever passed on timing luck.
    assert trace.index("close") > max(
        index for index, step in enumerate(trace) if step.startswith("write-end")
    ), f"the database was closed while a write was running: {trace}"
    assert "write-end:0" not in trace, "a write found the connection already closed"

    with sqlite3.connect(tmp_path / "test.db") as conn:
        stored = [row[0] for row in conn.execute("SELECT entity_id FROM states")]
    assert stored == ["light.inflight"], "the in-flight write must not be lost"


async def test_query_in_flight_is_not_cut_off_by_shutdown(jarvis):
    """A read must never turn into a silent empty result because of a close.

    Regression: `_execute_sync` returns [] when the connection is gone, and
    shutdown closed it without holding the lock, so a query already on a
    worker thread answered "no history" instead of the rows that existed.
    """
    import threading

    recorder = await _setup_recorder(jarvis)
    for index in range(5):
        jarvis.states.set(f"light.q{index}", STATE_ON)
    await recorder.async_commit()

    entered = threading.Event()
    real_execute = recorder._execute_sync

    def slow(sql, params):
        entered.set()
        time.sleep(0.25)
        return real_execute(sql, params)

    recorder._execute_sync = slow

    query = asyncio.ensure_future(recorder.states_between())
    for _ in range(200):
        if entered.is_set():
            break
        await asyncio.sleep(0.005)
    assert entered.is_set()

    await recorder.async_shutdown()
    rows = await query
    assert len(rows) == 5, "the read must finish against a live connection"


async def test_recorder_exposes_the_history_period_shape_the_api_calls(jarvis):
    """`jarvis/api/common.py` duck-types the recorder for this method.

    Regression: Recorder had none of the names that module looks for
    (async_history_period / async_history / async_get_history / history),
    so `GET /api/history/period` always answered with an empty list.
    """
    import inspect

    recorder = await _setup_recorder(jarvis)
    method = getattr(recorder, "async_history_period", None)
    assert callable(method), "the REST history endpoint needs this method"
    # The API layer binds these exact keyword arguments before calling.
    inspect.signature(method).bind(
        entity_ids=["light.kitchen"], start_time=None, end_time=None
    )

    jarvis.states.set("light.kitchen", STATE_ON)
    jarvis.states.set("light.kitchen", STATE_OFF)
    await recorder.async_commit()

    series = await method(entity_ids=["light.kitchen"], start_time=None, end_time=None)
    assert isinstance(series, list) and series, "one series per entity"
    assert [row["state"] for row in series[0]] == [STATE_ON, STATE_OFF]

    everything = await method()
    assert any(row["entity_id"] == "light.kitchen" for row in everything[0])


async def test_last_state_before_keys_match_states_between(jarvis):
    """Both halves of a merge must key on the same entity id.

    Regression: last_state_before echoed the caller's casing while
    states_between returns the stored (lower-case) id, so merging them —
    which is exactly what history does — produced two series for one entity.
    """
    recorder = await _setup_recorder(jarvis)
    jarvis.states.set("light.kitchen", STATE_ON)
    await recorder.async_commit()

    priors = await recorder.last_state_before(["LIGHT.KITCHEN"], time.time())
    assert list(priors) == ["light.kitchen"]


async def test_history_includes_prior_state_for_unfiltered_queries(jarvis):
    """"Everything" queries get the same start-of-window treatment as named ones.

    Regression: the prior-state lookup was skipped whenever no entity_id was
    given, so a whole-house graph opened at the first change in the window
    instead of at the value it was already sitting on.
    """
    recorder = await _setup_recorder(jarvis)
    await history_integration.async_setup(jarvis, {})

    jarvis.states.set("light.kitchen", STATE_ON)  # before the window
    await recorder.async_commit()
    await asyncio.sleep(0.02)
    boundary = time.time()
    await asyncio.sleep(0.02)
    jarvis.states.set("light.hall", STATE_ON)  # inside the window

    history = await history_integration.get_history(jarvis, None, start=boundary)
    assert [row["state"] for row in history["light.kitchen"]] == [STATE_ON], (
        "an entity that did not change in the window still has a value"
    )
    assert [row["state"] for row in history["light.hall"]] == [STATE_ON]


async def test_history_service_reports_the_window_it_actually_used(jarvis):
    """The response must describe the real query, not the caller's blanks.

    Regression: `start` came back as null whenever the caller omitted it,
    even though the query ran over `end - days`.
    """
    await _setup_recorder(jarvis)
    await history_integration.async_setup(jarvis, {"days": 3})
    jarvis.states.set("light.kitchen", STATE_ON)

    response = await jarvis.async_call_service(
        "history", "get", {"entity_id": "light.kitchen"}, return_response=True
    )
    assert response["start"] is not None
    start = datetime.fromisoformat(response["start"])
    end = datetime.fromisoformat(response["end"])
    assert (end - start).total_seconds() == pytest.approx(3 * DAY, abs=5)


async def test_logbook_keeps_rapid_changes_apart(jarvis):
    """Bursts of activity must not be collapsed by the merge step.

    Regression: the dedupe key rounded timestamps to 10 ms, so a scene or a
    script toggling something several times in one tick came back as two
    entries instead of six.
    """
    await _setup_recorder(jarvis)
    await logbook_integration.async_setup(jarvis, {"log_service_calls": False})
    logbook = jarvis.data["logbook"]

    for value in (STATE_ON, STATE_OFF, STATE_ON, STATE_OFF, STATE_ON, STATE_OFF):
        jarvis.states.set("light.flapping", value, {"friendly_name": "Flapping"})

    assert len(logbook.entries) == 6
    entries = await logbook.async_get()
    flapping = [e for e in entries if e["entity_id"] == "light.flapping"]
    assert len(flapping) == 6, "every real change survives the buffer/db merge"
    assert [e["state"] for e in flapping] == [
        STATE_ON, STATE_OFF, STATE_ON, STATE_OFF, STATE_ON, STATE_OFF
    ]


async def test_logbook_still_dedupes_the_same_change_from_both_sources(jarvis):
    """The precise key must not break the merge it exists for."""
    recorder = await _setup_recorder(jarvis)
    await logbook_integration.async_setup(jarvis, {"log_service_calls": False})
    logbook = jarvis.data["logbook"]

    jarvis.states.set("light.kitchen", STATE_OFF, {"friendly_name": "Kitchen Light"})
    jarvis.states.set("light.kitchen", STATE_ON, {"friendly_name": "Kitchen Light"})
    await logbook.async_log("was reset", name="Kitchen Light")
    await recorder.async_commit()

    entries = await logbook.async_get()
    assert len([e for e in entries if e["message"] == "turned on"]) == 1
    assert len([e for e in entries if e["message"] == "was reset"]) == 1


async def test_logbook_skips_service_calls_whose_targets_are_filtered_out(jarvis):
    """An excluded entity must not leak back in via the service-call feed.

    Regression: the target list was filtered but the entry was appended
    anyway, so `sensor.*` calls showed up in a feed configured to exclude
    the sensor domain.
    """
    await logbook_integration.async_setup(
        jarvis, {"log_service_calls": True, "exclude": {"domains": ["sensor"]}}
    )
    logbook = jarvis.data["logbook"]

    jarvis.services.register("sensor", "poke", lambda call: None)
    jarvis.services.register("light", "turn_on", lambda call: None)

    await jarvis.async_call_service("sensor", "poke", {"entity_id": "sensor.hidden"})
    assert not any("sensor.poke" in e["message"] for e in logbook.entries)

    # A call with no entity target at all is still worth reporting.
    await jarvis.async_call_service("sensor", "poke", {})
    assert any("sensor.poke" in e["message"] for e in logbook.entries)

    await jarvis.async_call_service("light", "turn_on", {"entity_id": "light.kitchen"})
    assert any("light.turn_on" in e["message"] for e in logbook.entries)


async def test_unknown_solar_event_is_rejected_not_silently_sunset():
    """A typo must fail loudly instead of firing hours off.

    Regression: next_event fell back to the sunset zenith *and* the sunset
    branch for any unrecognised name, so `sunris` resolved to sunset.
    """
    when = datetime(2024, 6, 21, 17, 0, tzinfo=timezone.utc)
    for bad in ("sunris", "banana", "sun set", ""):
        with pytest.raises(ValueError):
            solar.next_event(40.0, -74.0, bad, when)

    # Every advertised name still resolves, case-insensitively.
    for good in sun_integration.EVENTS:
        assert solar.next_event(40.0, -74.0, good.upper(), when) is not None


async def test_sun_data_next_rejects_unknown_events(jarvis):
    await sun_integration.async_setup(jarvis, {})
    data = sun_integration.get_sun(jarvis)
    with pytest.raises(ValueError):
        data.next("sunsett")
    assert data.next("sunset") is not None
