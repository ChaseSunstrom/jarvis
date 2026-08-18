"""How a coding agent changes a file — the part that is silently wrong.

Its own file because this is the primitive everything else in Jarvis Code sits
on, and the failure mode is not a crash. A wrong edit applies cleanly, the
tests may even still pass, and the mistake is found weeks later by somebody
reading the diff.

The rule under test: an edit is `(old, new)` and `old` must appear **exactly
once**. Not "the first occurrence" — if it appears twice the model has not said
which one it means, and picking one is a coin flip that lands silently on the
wrong line.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.code.edits import (  # noqa: E402
    EditError,
    apply_edit,
    numbered,
    search_text,
)

SOURCE = """def add(a, b):
    return a + b


def multiply(a, b):
    return a * b
"""


# --- the rule -------------------------------------------------------------------

def test_a_unique_snippet_is_replaced():
    result = apply_edit(SOURCE, "return a + b", "return a + b + 0")
    assert "return a + b + 0" in result.text
    assert "return a * b" in result.text  # nothing else touched
    assert result.how == "exact"


def test_an_ambiguous_snippet_is_refused_rather_than_guessed():
    """The single most important refusal in Jarvis Code.

    `x = 1` twice in a file and a model that says "change x = 1" has not said
    which. Editing the first is a coin flip, it applies cleanly, and the wrong
    line is now wrong for ever.
    """
    source = "x = 1\ny = 2\nx = 1\n"
    with pytest.raises(EditError) as caught:
        apply_edit(source, "x = 1", "x = 3")
    assert "2 times" in str(caught.value)
    # And it says what to do about it, because the reader is a model that will
    # try again.
    assert "surrounding lines" in str(caught.value)


def test_ambiguity_is_resolved_by_more_context_rather_than_by_an_index():
    source = "x = 1\ny = 2\nx = 1\n"
    result = apply_edit(source, "y = 2\nx = 1", "y = 2\nx = 3")
    assert result.text == "x = 1\ny = 2\nx = 3\n"


def test_replacing_every_occurrence_has_to_be_asked_for():
    # The default cannot be "all": a model that under-specified a snippet would
    # rewrite every similar line and the diff would look deliberate.
    source = "x = 1\nx = 1\nx = 1\n"
    with pytest.raises(EditError):
        apply_edit(source, "x = 1", "x = 2")
    result = apply_edit(source, "x = 1", "x = 2", expect=3)
    assert result.text == "x = 2\nx = 2\nx = 2\n"


def test_asking_to_replace_more_than_are_there_is_refused_not_under_applied():
    """The silent under-edit.

    Two occurrences and `expect=3`: falling through to the loose rung would
    replace ONE of them and report success, which is the same class of quiet
    wrongness as picking an occurrence at random.
    """
    source = "x = 1\nx = 1\n"
    with pytest.raises(EditError, match="2 times"):
        apply_edit(source, "x = 1", "x = 2", expect=1)
    with pytest.raises(EditError) as caught:
        apply_edit(source, "x = 1", "x = 2", expect=3)
    assert "x = 2" not in source
    assert "2 times" in str(caught.value)


def test_text_that_is_not_there_says_to_read_the_file_again():
    with pytest.raises(EditError, match="not in the file"):
        apply_edit(SOURCE, "return a - b", "return 0")


def test_an_empty_or_pointless_edit_is_refused():
    with pytest.raises(EditError, match="empty"):
        apply_edit(SOURCE, "", "x")
    with pytest.raises(EditError, match="identical"):
        apply_edit(SOURCE, "return a + b", "return a + b")


# --- what "exactly once" has to survive ---------------------------------------------

def test_a_snippet_quoted_back_with_spaces_instead_of_tabs_still_matches():
    """Models do not reproduce tabs. A matcher that insists on them fails
    constantly on edits that are perfectly correct."""
    source = "def f():\n\treturn 1\n"
    result = apply_edit(source, "def f():\n    return 1", "def f():\n    return 2")
    assert "return 2" in result.text
    assert result.how == "whitespace-insensitive"


def test_trailing_whitespace_in_the_file_does_not_defeat_a_match():
    source = "x = 1   \ny = 2\n"
    result = apply_edit(source, "x = 1", "x = 9")
    assert result.text.startswith("x = 9")


def test_crlf_line_endings_do_not_defeat_a_match():
    source = "a = 1\r\nb = 2\r\n"
    result = apply_edit(source, "a = 1\nb = 2", "a = 3\nb = 4")
    assert "a = 3" in result.text and "b = 4" in result.text


def test_the_ladder_still_insists_on_uniqueness():
    """Loosening the match must not loosen the RULE.

    Both occurrences are tab-indented and the model quoted spaces, so nothing
    matches exactly and the loose rung finds two. Two places is two places, and
    picking one is the same coin flip as before.
    """
    source = "def f():\n\treturn 1\n\ndef g():\n\treturn 1\n"
    with pytest.raises(EditError, match="whitespace is ignored"):
        apply_edit(source, "    return 1", "    return 2")


def test_a_unique_exact_match_wins_even_when_a_whitespace_variant_exists():
    """Exact-first, and it wins outright.

    Refusing here — on the grounds that a tab-indented cousin exists elsewhere
    — would break correct edits in every file with mixed indentation. A literal
    unique match is the most predictable rule available.
    """
    source = "def f():\n\treturn 1\n\ndef g():\n    return 1\n"
    result = apply_edit(source, "    return 1", "    return 2")
    assert result.how == "exact"
    assert result.text == "def f():\n\treturn 1\n\ndef g():\n    return 2\n"


def test_indentation_depth_is_not_ignored():
    """Two blocks at different depths are usually genuinely different blocks.

    One inside an `if`, one after it. A matcher that conflated them would edit
    the wrong branch — so the normalisation collapses tabs and trailing space
    and stops there.
    """
    source = "if x:\n    do_it()\ndo_it()\n"
    result = apply_edit(source, "    do_it()", "    do_it_twice()")
    assert result.text == "if x:\n    do_it_twice()\ndo_it()\n"


def test_a_loose_match_splices_on_line_boundaries_and_loses_nothing():
    # The off-by-one that eats a newline leaves the file one line shorter with
    # no error anywhere.
    source = "one\n\ttwo\nthree\nfour\n"
    result = apply_edit(source, "    two\nthree", "TWO\nTHREE")
    assert result.text == "one\nTWO\nTHREE\nfour\n"
    assert result.text.count("\n") == source.count("\n")


def test_an_edit_at_the_very_start_of_a_file_works():
    result = apply_edit("first\nsecond\n", "first", "FIRST")
    assert result.text == "FIRST\nsecond\n"
    assert result.line == 1


def test_an_edit_at_the_very_end_of_a_file_works():
    result = apply_edit("first\nsecond\n", "second", "SECOND")
    assert result.text == "first\nSECOND\n"


def test_the_reported_line_is_where_the_change_actually_is():
    # It goes into the report a human reads, so a wrong one is worse than none.
    result = apply_edit(SOURCE, "return a * b", "return a * b * 1")
    assert result.line == 6


def test_a_multi_line_insertion_keeps_the_rest_of_the_file():
    result = apply_edit(SOURCE, "def multiply(a, b):", "def divide(a, b):\n    return a / b\n\n\ndef multiply(a, b):")
    assert "def add(a, b):" in result.text
    assert "def divide(a, b):" in result.text
    assert "def multiply(a, b):" in result.text


# --- reading and searching ------------------------------------------------------------

def test_numbering_is_what_lets_a_model_name_a_place():
    # Without it, "the second loop" is a thing nobody can point at and nobody
    # can check afterwards.
    out = numbered("a\nb\nc")
    assert out.splitlines()[0].strip().startswith("1")
    assert out.splitlines()[2].strip().startswith("3")


def test_numbering_can_start_part_way_down_a_file():
    out = numbered("x\ny", start=100)
    assert out.splitlines()[0].startswith("100")
    assert out.splitlines()[1].startswith("101")


def test_numbering_pads_so_the_text_lines_up():
    out = numbered("\n".join("x" * 1 for _ in range(10)))
    first, last = out.splitlines()[0], out.splitlines()[-1]
    assert first.index("\t") == last.index("\t")


def test_search_reports_line_numbers():
    hits = search_text("m.py", SOURCE, r"def \w+")
    assert [(h.line, h.path) for h in hits] == [(1, "m.py"), (5, "m.py")]


def test_search_is_bounded():
    hits = search_text("m.py", "match\n" * 500, "match", limit=10)
    assert len(hits) == 10


def test_a_pattern_a_model_wrote_badly_is_a_refusal_not_a_crash():
    # `(` is a thing models write.
    with pytest.raises(EditError, match="regular expression"):
        search_text("m.py", SOURCE, "def (")
