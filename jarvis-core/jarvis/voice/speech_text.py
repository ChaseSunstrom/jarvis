"""What a reply looks like when it is said rather than shown (M73).

The model writes for a screen: bold, bullets, ``~$78,721``, ``0.15%``,
``40 GB``, ``49/100``. Piper says what it is given, so the house read
"asterisk asterisk price colon asterisk asterisk tilde dollar seventy-eight
thousand…" to the operator on 26 Aug 2026. The transcript keeps the reply as
written; this is the form the synthesiser gets.

Deliberately a table of expansions, not a grammar: every rule names the
case it exists for, and a symbol not in the table is left alone rather than
guessed at. What it does NOT do: read acronyms (Piper's own rule stands),
convert units it was not taught, or touch anything inside the transcript.
"""

from __future__ import annotations

import re

__all__ = ["spoken_form"]

#: Markdown that carries no sound. Order matters: links before emphasis (a
#: link's text may be bold), fences before inline code.
_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)\s]+)\)")
_IMAGE = re.compile(r"!\[[^\]]*\]\((?:[^)\s]+)\)")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.M)
_BULLET = re.compile(r"^[ \t]*(?:[-*+•]|\d+[.)])[ \t]+", re.M)
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1")
_STRIKE = re.compile(r"~~(.+?)~~")
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$", re.M)
_TABLE_PIPES = re.compile(r"^[ \t]*\|(.*)\|[ \t]*$", re.M)

#: A bare address is read as its host — "news dot bitcoin dot com" says
#: where; forty characters of path say nothing a listener can use.
_URL = re.compile(r"\bhttps?://([^\s/)>\]]+)(?:[^\s)>\]]*)")

#: Money. The sign comes first in writing and last in speech; a suffix that
#: means thousand/million/billion is spelled out, because "two B dollars"
#: is not a sum anyone says.
_MONEY = re.compile(
    r"(?P<sign>[$£€])(?P<num>\d[\d,]*(?:\.\d+)?)(?P<suffix>[kKmMbB]n?|bn|tn)?\b"
)
_CURRENCY = {"$": "dollars", "£": "pounds", "€": "euros"}
_MAGNITUDE = {"k": "thousand", "m": "million", "b": "billion", "bn": "billion", "tn": "trillion"}

_ABOUT = re.compile(r"~\s*(?=[$£€]?\d)")
_PERCENT = re.compile(r"(?<=\d)\s?%")
_RATIO = re.compile(r"\b(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\b")
_UNITS = {
    "TB": "terabytes", "GB": "gigabytes", "MB": "megabytes", "KB": "kilobytes",
    "kWh": "kilowatt hours", "kW": "kilowatts", "MWh": "megawatt hours", "MW": "megawatts",
    "W": "watts", "V": "volts", "A": "amps", "Hz": "hertz", "kHz": "kilohertz", "GHz": "gigahertz",
    "km/h": "kilometres per hour", "mph": "miles per hour", "km": "kilometres", "kg": "kilograms",
    "ms": "milliseconds", "°C": "degrees Celsius", "°F": "degrees Fahrenheit", "°": "degrees",
}
_UNIT = re.compile(
    r"(?<=\d)\s?(" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True)) + r")(?![A-Za-z])"
)
_AMPERSAND = re.compile(r"\s&\s")
_ARROW = re.compile(r"\s*(?:->|→)\s*")
_TIMES = re.compile(r"(?<=\d)\s?[×x]\s?(?=\d)")
#: "2x faster" — a multiplier with nothing after it is "times", not the letter.
_TIMES_ALONE = re.compile(r"(?<=\d)x\b")
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF⬀-⯿️]"
)
_SPACES = re.compile(r"[ \t]+")


def _money(match: re.Match[str]) -> str:
    num = match.group("num")
    suffix = (match.group("suffix") or "").lower()
    magnitude = _MAGNITUDE.get(suffix, "")
    unit = _CURRENCY[match.group("sign")]
    if num in ("1", "1.0") and not magnitude:
        unit = unit[:-1]
    return f"{num} {magnitude} {unit}".replace("  ", " ")


def _unit(match: re.Match[str]) -> str:
    return " " + _UNITS[match.group(1)]


def spoken_form(text: str) -> str:
    """`text` as words a synthesiser can say. The transcript keeps the original."""
    out = str(text or "")
    if not out.strip():
        return ""
    out = _FENCE.sub(" ", out)
    out = _IMAGE.sub(" ", out)
    out = _LINK.sub(r"\1", out)
    out = _URL.sub(lambda m: m.group(1).replace(".", " dot "), out)
    out = _INLINE_CODE.sub(r"\1", out)
    out = _TABLE_RULE.sub("", out)
    out = _TABLE_PIPES.sub(lambda m: m.group(1).replace("|", ","), out)
    out = _HEADING.sub("", out)
    out = _BULLET.sub("", out)
    out = _STRIKE.sub(r"\1", out)
    for _ in range(3):  # nested emphasis: ***x***, **_x_**
        out = _EMPHASIS.sub(r"\2", out)
    out = _EMOJI.sub("", out)
    out = _ABOUT.sub("about ", out)
    out = _MONEY.sub(_money, out)
    out = _PERCENT.sub(" percent", out)
    out = _UNIT.sub(_unit, out)
    out = _RATIO.sub(r"\1 out of \2", out)
    out = _TIMES.sub(" by ", out)
    out = _TIMES_ALONE.sub(" times", out)
    out = _AMPERSAND.sub(" and ", out)
    out = _ARROW.sub(" to ", out)
    out = _SPACES.sub(" ", out)
    return "\n".join(line.strip() for line in out.splitlines()).strip()
