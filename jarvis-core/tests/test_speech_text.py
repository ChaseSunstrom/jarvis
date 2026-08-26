"""M73 — said, not shown: the reply the synthesiser gets is words.

The operator's own turn, 26 Aug 2026: a Bitcoin briefing written for the
screen — bold, bullets, "~$78,721", "0.15%", "40 GB", "49/100" — and Piper
read the asterisks and the dollar signs. Every row here is a case that was
heard wrong or would be; the transcript is untouched by any of it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.voice.pipeline import speakable  # noqa: E402
from jarvis.voice.speech_text import spoken_form  # noqa: E402

CASES = [
    ("**Price:** ~$78,721, up 0.15% over 24 hours.", "Price: about 78,721 dollars, up 0.15 percent over 24 hours."),
    ("Sentiment is neutral (49/100).", "Sentiment is neutral (49 out of 100)."),
    ("- **Thailand** is rewriting its rules.\n- **Strategy (MSTR)** sold $2B of shares.",
     "Thailand is rewriting its rules.\nStrategy (MSTR) sold 2 billion dollars of shares."),
    ("A $1.59B cash pile, a $80k rally, £5 and €1.", "A 1.59 billion dollars cash pile, a 80 thousand dollars rally, 5 pounds and 1 euro."),
    ("The database shrank by 40 GB; the file is 12 MB.", "The database shrank by 40 gigabytes; the file is 12 megabytes."),
    ("It is 21.5°C outside and the meter reads 3.2 kW, 14 kWh today.",
     "It is 21.5 degrees Celsius outside and the meter reads 3.2 kilowatts, 14 kilowatt hours today."),
    ("See https://news.bitcoin.com/markets/x and [the report](https://a.example/r).",
     "See news dot bitcoin dot com and the report."),
    ("# Summary\n\n1. First point\n2. Second point", "Summary\n\nFirst point\nSecond point"),
    ("Use `docker compose up` then wait.", "Use docker compose up then wait."),
    ("Wind at 3 km/h & rising → calm by 2x.", "Wind at 3 kilometres per hour and rising to calm by 2 times."),
    ("A 3 x 4 grid, ***very*** _quiet_.", "A 3 by 4 grid, very quiet."),
    ("Done ✅ — the lamp is on 💡.", "Done — the lamp is on ."),
    ("", ""),
]


@pytest.mark.parametrize("written,said", CASES)
def test_the_spoken_form_of_what_was_written(written: str, said: str) -> None:
    assert spoken_form(written) == said


def test_what_is_left_alone() -> None:
    # No rule fires: the text is the text, so a reply with none of the above
    # reaches the synthesiser exactly as before M73.
    assert spoken_form("The kitchen light is on, Sir. Good night.") == "The kitchen light is on, Sir. Good night."
    # Acronyms are Piper's own business; a lone symbol the table does not know stays.
    assert spoken_form("MSTR closed at 5 ¤.") == "MSTR closed at 5 ¤."


def test_speakable_says_it_rather_than_shows_it() -> None:
    """The pipeline's one door to the synthesiser: chunks, the whole reply
    and the remainder all pass through `speakable`, so this is the guarantee
    that no surface hears an asterisk."""
    assert speakable("**Price:** ~$78,721.\n\nUp 0.15%.") == "Price: about 78,721 dollars. Up 0.15 percent."
    assert speakable("Done, Sir.") == "Done, Sir."
    assert speakable("...?") == ""
