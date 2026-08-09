"""Turning a state change into a sentence a person would actually say.

Pure functions, no model call. "Motion detected at the front door" is not a
hard sentence to write; it is a hard sentence to *guarantee*, and an LLM in
this path would make every doorbell chime a token spend and a latency risk.
The common cases are a table.

Two shapes cover almost everything:

* **Place** — for things that happen *somewhere*: motion, smoke, water.
  "Motion detected at the **front door**". The place is the entity's area if
  it has one, otherwise its name with the device-class word taken off
  ("Front Door Motion" -> "front door").
* **Subject** — for things that happen *to something*: doors, batteries,
  connectivity. "The **garage door** has opened". Here the whole name is the
  subject, because "the garage has opened" is not what happened.

Numbers get "{name} is now {value} {unit}", with the unit said as a word:
"Kitchen temperature is now 24 degrees", not "24 °C".

Names come off devices and out of YAML, so they are softened rather than
trusted: lower-cased word by word (keeping CO2, PM2.5, TVOC intact) and the
finished sentence is capitalised once at the front.
"""

from __future__ import annotations

import re
from typing import Any

from ...const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN

#: Sentences for a change that happened somewhere. (on, off)
PLACE_PHRASES: dict[str, tuple[str, str | None]] = {
    "motion": ("Motion detected at the {place}", "Motion has stopped at the {place}"),
    "occupancy": ("The {place} is occupied", "The {place} is empty again"),
    "presence": ("Someone is at the {place}", "Nobody is at the {place} any more"),
    "smoke": ("Smoke detected at the {place}", "The smoke alarm at the {place} has cleared"),
    "gas": ("Gas detected at the {place}", "The gas alarm at the {place} has cleared"),
    "carbon_monoxide": (
        "Carbon monoxide detected at the {place}",
        "The carbon monoxide alarm at the {place} has cleared",
    ),
    "moisture": ("Water detected at the {place}", "The {place} is dry again"),
    "sound": ("Sound detected at the {place}", "Sound has stopped at the {place}"),
    "vibration": (
        "Vibration detected at the {place}",
        "Vibration has stopped at the {place}",
    ),
    "tamper": (
        "Something is tampering with the sensor at the {place}",
        "The tamper alert at the {place} has cleared",
    ),
    "light": ("There is light at the {place}", "It has gone dark at the {place}"),
}

#: Sentences about a thing rather than a place. (on, off)
SUBJECT_PHRASES: dict[str, tuple[str, str | None]] = {
    "door": ("The {subject} has opened", "The {subject} has closed"),
    "garage_door": ("The {subject} has opened", "The {subject} has closed"),
    "window": ("The {subject} has opened", "The {subject} has closed"),
    "opening": ("The {subject} has opened", "The {subject} has closed"),
    "lock": ("The {subject} has been unlocked", "The {subject} has been locked"),
    "connectivity": ("The {subject} is back online", "The {subject} has gone offline"),
    "problem": ("The {subject} is reporting a problem", "The {subject} is back to normal"),
    "battery": ("The {subject} is low", "The {subject} is back to normal"),
    "battery_charging": ("The {subject} is charging", "The {subject} has stopped charging"),
    "running": ("The {subject} has started", "The {subject} has finished"),
    "power": ("The {subject} has power again", "The {subject} has lost power"),
    "plug": ("The {subject} is plugged in", "The {subject} has been unplugged"),
    "cold": ("The {subject} is cold", "The {subject} is no longer cold"),
    "heat": ("The {subject} is hot", "The {subject} is no longer hot"),
    "safety": ("The {subject} is unsafe", "The {subject} is safe again"),
}

DEFAULT_BINARY = ("The {subject} is on", "The {subject} is off")

UNAVAILABLE_PHRASE = "The {subject} has stopped reporting"

#: Extra words worth removing when a name is turned into a place.
CLASS_WORDS: dict[str, tuple[str, ...]] = {
    "motion": ("motion", "pir", "movement"),
    "occupancy": ("occupancy", "occupied"),
    "presence": ("presence",),
    "smoke": ("smoke",),
    "gas": ("gas",),
    "carbon_monoxide": ("carbon", "monoxide", "co"),
    "moisture": ("moisture", "leak", "water", "damp", "flood"),
    "sound": ("sound", "noise"),
    "vibration": ("vibration",),
    "tamper": ("tamper",),
}

#: Units said out loud. Anything missing is read as-is.
UNIT_WORDS: dict[str, str] = {
    "°c": "degrees",
    "°f": "degrees",
    "c": "degrees",
    "f": "degrees",
    "k": "kelvin",
    "%": "percent",
    "lx": "lux",
    "lux": "lux",
    "ppm": "parts per million",
    "ppb": "parts per billion",
    "w": "watts",
    "kw": "kilowatts",
    "wh": "watt hours",
    "kwh": "kilowatt hours",
    "v": "volts",
    "a": "amps",
    "ma": "milliamps",
    "hz": "hertz",
    "hpa": "hectopascals",
    "mbar": "millibars",
    "db": "decibels",
    "dbm": "decibel milliwatts",
    "µg/m³": "micrograms per cubic metre",
    "mm": "millimetres",
    "cm": "centimetres",
    "m": "metres",
    "km/h": "kilometres per hour",
    "kg": "kilograms",
    "g": "grams",
    "l": "litres",
    "m³": "cubic metres",
    "s": "seconds",
}

TRUEISH = frozenset({STATE_ON, "true", "open", "opened", "detected", "home", "yes", "1"})
FALSEISH = frozenset({STATE_OFF, "false", "closed", "clear", "not_home", "no", "0"})

MAX_SENTENCE_CHARS = 240


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------
def _keep_case(word: str) -> bool:
    """True for CO2, PM2.5, TVOC, McKay — things lower-casing would spoil."""
    stripped = "".join(ch for ch in word if ch.isalpha())
    if not stripped:
        return True
    if len(stripped) > 1 and stripped.isupper():
        return True
    return word != word.title() and word != word.lower()


def soften(name: Any) -> str:
    """Lower-case a friendly name so it can sit mid-sentence."""
    words = re.sub(r"\s+", " ", str(name or "").strip()).split(" ")
    return " ".join(word if _keep_case(word) else word.lower() for word in words if word)


def sentence_case(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def format_value(value: Any) -> str:
    """``24.0`` -> ``"24"``; anything unparseable comes back unchanged."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return str(value)
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return f"{number:g}"


def unit_words(unit: Any) -> str:
    text = str(unit or "").strip()
    if not text:
        return ""
    return UNIT_WORDS.get(text.lower(), text)


def place_from(name: Any, area: Any, device_class: Any) -> str:
    """Where this happened: the area, or the name minus its class word."""
    if area:
        return soften(area)
    words = soften(name).split()
    drop = set(CLASS_WORDS.get(str(device_class or ""), ()))
    drop.add("sensor")
    while words and words[-1].lower() in drop:
        words.pop()
    while words and words[0].lower() in drop:
        words.pop(0)
    return " ".join(words) or soften(name)


def is_on(state: Any) -> bool | None:
    text = str(state or "").strip().lower()
    if text in TRUEISH:
        return True
    if text in FALSEISH:
        return False
    return None


# ---------------------------------------------------------------------------
# the generator
# ---------------------------------------------------------------------------
def describe(
    *,
    name: Any,
    new_state: Any,
    domain: str = "",
    device_class: Any = None,
    area: Any = None,
    unit: Any = None,
) -> str | None:
    """One sentence for this change, or ``None`` when there is nothing to say.

    Only the *new* state matters: a sentence that also reported where the
    value came from ("now 24, up from 22") would no longer be the sentence
    people actually say, and the transition is the caller's business anyway —
    it is what decided there was something worth saying at all.
    """
    state = str(new_state or "").strip()
    lowered = state.lower()
    subject = soften(name)
    if not subject:
        return None

    if lowered == STATE_UNAVAILABLE:
        return _finish(UNAVAILABLE_PHRASE.format(subject=subject))
    if lowered in (STATE_UNKNOWN, ""):
        return None

    device_class = str(device_class or "").strip().lower() or None
    place = place_from(name, area, device_class)

    on = is_on(state)
    binary = domain == "binary_sensor" or (domain != "sensor" and on is not None)
    if binary:
        if on is None:
            return None
        index = 0 if on else 1
        phrases = PLACE_PHRASES.get(device_class or "")
        if phrases is not None:
            template = phrases[index]
            return _finish(template.format(place=place)) if template else None
        phrases = SUBJECT_PHRASES.get(device_class or "", DEFAULT_BINARY)
        template = phrases[index]
        return _finish(template.format(subject=subject)) if template else None

    # A reading rather than a change of condition.
    words = unit_words(unit)
    value = format_value(state)
    if words:
        return _finish(f"{subject} is now {value} {words}")
    return _finish(f"{subject} is now {value}")


def _finish(sentence: str) -> str:
    return sentence_case(re.sub(r"\s+", " ", sentence).strip())[:MAX_SENTENCE_CHARS]


__all__ = [
    "CLASS_WORDS",
    "DEFAULT_BINARY",
    "PLACE_PHRASES",
    "SUBJECT_PHRASES",
    "UNIT_WORDS",
    "describe",
    "format_value",
    "is_on",
    "place_from",
    "sentence_case",
    "soften",
    "unit_words",
]
