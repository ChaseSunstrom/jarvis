"""Reviewing an automation without unlocking a door to find out.

## Why this exists

There was no way to test an automation except `automation.trigger`, which
actuates the house. Asking "is this rule right?" meant finding out by running
it, which is precisely what somebody unsure about a rule does not want to do —
especially the rules worth being unsure about, which are the ones that touch
locks and messages.

Meanwhile `authored.validate` checks shape and the trigger platform and stops,
deliberately: service names, entity ids and templates are "decided at run
time". So

    {"service": "lite.turn_on", "target": {"entity_id": "light.kitchn"}}

saved without complaint, listed looking correct, and failed silently forever.
Two typos, no feedback.

`automation.check` is the report that closes that. It is **not** a gate: a
service belonging to an integration that loads later is legitimate, and an
entity can appear at any time, so the engine genuinely cannot refuse this. What
it can do is say what looks wrong and let a human decide.

The levels carry that distinction and are the point of the design:

  * **error** — cannot work as written. A service that does not exist will not
    start existing by being called.
  * **warning** — looks wrong, might not be. An entity id absent from the
    registry is the ordinary state of a bulb not yet plugged in, and refusing
    it would make this useless on a house still being set up.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.automation.check import ERROR, WARNING, check  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402


@pytest.fixture
async def jarvis(tmp_path):
    instance = Jarvis(tmp_path)
    await instance.async_setup({"automation": [], "demo": {}})
    yield instance
    await instance.async_stop()


def _levels(report: dict) -> list[str]:
    return [f["level"] for f in report["findings"]]


def _at(report: dict, where: str) -> list[dict]:
    return [f for f in report["findings"] if f["where"] == where]


# ---------------------------------------------------------------------------
# the typos that used to be silent
# ---------------------------------------------------------------------------
async def test_a_misspelled_service_is_an_error(jarvis):
    """The highest-value catch: unambiguous, and silent at run time.

    `lite.turn_on` for `light.turn_on` is a typo nothing else in the system
    will ever mention.
    """
    report = check(
        jarvis, {"alias": "x", "trigger": [], "action": [{"service": "lite.turn_on"}]}
    )

    assert report["ok"] is False
    finding = _at(report, "action")[0]
    assert finding["level"] == ERROR
    assert "lite.turn_on" in finding["message"]


async def test_a_misspelled_service_in_a_known_domain_lists_the_real_ones(jarvis):
    """Naming the alternatives is what turns a complaint into a fix."""
    report = check(
        jarvis, {"alias": "x", "trigger": [], "action": [{"service": "light.turn_onn"}]}
    )

    message = _at(report, "action")[0]["message"]
    assert "turn_on" in message, "the near miss was not offered"


async def test_an_unknown_entity_is_a_warning_not_an_error(jarvis):
    """A bulb that is not plugged in yet is a real thing to write a rule for.

    Erroring here would make the report useless on exactly the house that most
    needs it — one that is still being set up.
    """
    report = check(
        jarvis,
        {
            "alias": "x",
            "trigger": [],
            "action": [
                {"service": "light.turn_on", "target": {"entity_id": "light.kitchn"}}
            ],
        },
    )

    assert report["ok"] is True, "a missing entity blocked the automation"
    assert _at(report, "entity_id")[0]["level"] == WARNING


async def test_a_template_that_will_not_compile_is_an_error(jarvis):
    report = check(
        jarvis,
        {
            "alias": "x",
            "trigger": [],
            "action": [
                {"service": "light.turn_on", "data_template": {"x": "{{ unclosed "}}
            ],
        },
    )

    assert report["ok"] is False
    assert _at(report, "template")[0]["level"] == ERROR


async def test_a_good_automation_says_nothing_at_all(jarvis):
    """A report that always finds something is a report nobody reads."""
    report = check(
        jarvis,
        {
            "alias": "Bedtime",
            "trigger": [{"platform": "time", "at": "23:00:00"}],
            "action": [
                {"service": "light.turn_off", "target": {"entity_id": "light.bed_light"}}
            ],
        },
    )

    assert report["ok"] is True
    assert report["findings"] == []


# ---------------------------------------------------------------------------
# what it declines to do
# ---------------------------------------------------------------------------
async def test_a_templated_service_name_is_not_guessed_at(jarvis):
    """`service: "{{ whatever }}"` is decided at run time, after this runs.

    Reporting it as missing would be a false alarm on the one shape that is
    legitimately undecidable, and this module's promise is that everything it
    reports is worth looking at.
    """
    report = check(
        jarvis,
        {"alias": "x", "trigger": [], "action": [{"service": "{{ chosen_service }}"}]},
    )

    assert _at(report, "action") == []


async def test_it_says_what_the_automation_would_be_allowed_to_touch(jarvis):
    """The same sentence the approval card uses, so there is one answer."""
    report = check(
        jarvis,
        {
            "alias": "x",
            "trigger": [],
            "action": [
                {"service": "lock.unlock", "target": {"entity_id": "lock.front_door"}}
            ],
        },
    )

    assert "lock" in report["reach"]


async def test_an_automation_with_no_trigger_is_noted_but_allowed(jarvis):
    """Legitimate for something run by hand or from a script."""
    report = check(
        jarvis, {"alias": "x", "action": [{"service": "light.turn_on"}]}
    )

    assert report["ok"] is True
    assert _at(report, "trigger")[0]["level"] == WARNING


async def test_nothing_is_executed(jarvis):
    """The whole premise. A check that actuated would be a trigger.

    A service that records being called, named in the action list, must not be
    called — including through the nesting the walker descends into.
    """
    ran: list = []
    jarvis.services.register("demo", "record", lambda call: ran.append(call))

    check(
        jarvis,
        {
            "alias": "x",
            "trigger": [],
            "action": [
                {"service": "demo.record"},
                {"repeat": {"count": 3, "sequence": [{"service": "demo.record"}]}},
            ],
        },
    )

    assert ran == []


# ---------------------------------------------------------------------------
# through the service, which is how the console reaches it
# ---------------------------------------------------------------------------
async def test_the_service_reviews_a_draft_before_it_is_saved(jarvis):
    report = await jarvis.async_call_service(
        "automation",
        "check",
        {"config": {"alias": "x", "trigger": [], "action": [{"service": "nope.nope"}]}},
        return_response=True,
    )

    assert report["ok"] is False


async def test_the_service_reviews_an_automation_that_already_exists(jarvis):
    manager = jarvis.data["automation"]
    automation = await manager.async_add(
        {"id": "a", "alias": "Existing", "trigger": [], "action": [{"service": "no.no"}]}
    )

    report = await jarvis.async_call_service(
        "automation",
        "check",
        {"entity_id": automation.entity_id},
        return_response=True,
    )

    assert report[automation.entity_id]["ok"] is False


async def test_the_service_asks_for_something_to_check(jarvis):
    """Called with neither, it says which two arguments it takes.

    `manager.resolve` deliberately matches nothing on an empty target — fanning
    out to every automation on a missing `entity_id` is a blast radius that has
    to be asked for by name — so without this the reply would be an empty dict
    that reads like "nothing wrong".
    """
    report = await jarvis.async_call_service(
        "automation", "check", {}, return_response=True
    )

    assert report["ok"] is False
    assert "entity_id" in report["findings"][0]["message"]
