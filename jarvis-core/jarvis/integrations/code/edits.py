"""How a coding agent changes a file, and the one rule that makes it safe.

Pure: strings in, strings out. No filesystem, no model, no repo — so the part
that decides *what the file becomes* is testable exhaustively, and it is the
part where a coding agent is wrong in ways nobody notices until later.

## Replace, never rewrite

The tempting design is "the model returns the new file". It is easy, it always
applies, and it is the worst option available: a model asked to reproduce four
hundred lines to change one of them will drop a function, reformat an unrelated
block, or quietly lose the bit it did not understand — and the diff looks like
a big change because it *is* a big change, so nobody can see which part was
meant.

So an edit is `(old, new)` and `old` must appear **exactly once**. Not "the
first occurrence": if `old` appears twice, the model has not said which one it
means, and picking one is a coin flip that lands silently on the wrong line.
That single rule is most of what separates an agent that can be trusted with a
repo from one that cannot.

## What "exactly once" has to survive

Real files have tabs, CRLF endings and trailing whitespace, and a model quoting
a snippet back reproduces none of those reliably. A matcher that is strict about
them fails constantly on correct edits; one that ignores them entirely can match
text that is not there. The answer here is a **ladder**: exact first, then a
whitespace-insensitive comparison that still requires uniqueness, and nothing
looser than that. Every rung reports which one matched, so a caller can say so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MAX_FILE_BYTES",
    "EditError",
    "EditResult",
    "apply_edit",
    "count_matches",
    "line_of",
    "numbered",
    "search_text",
]

#: Beyond this a file is not something to hand a model whole. It is also the
#: size at which "read it, then replace a string in it" stops being a sensible
#: way to change anything.
MAX_FILE_BYTES = 400_000

#: How the match was found, from strictest to loosest.
EXACT = "exact"
WHITESPACE = "whitespace-insensitive"


class EditError(ValueError):
    """An edit that must not be applied, with the reason a model can act on."""


@dataclass
class EditResult:
    text: str
    #: Which rung of the ladder matched.
    how: str
    #: 1-based line the change starts on, for the report.
    line: int


def _normalise(text: str) -> str:
    """Collapse the differences a model cannot reproduce reliably.

    Tabs, trailing spaces and CRLF. NOT indentation depth and NOT blank lines:
    two blocks that differ only in how deeply they are indented are usually
    genuinely different blocks — one inside an `if`, one after it — and a
    matcher that conflated them would edit the wrong branch.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.replace("\t", "    ").rstrip() for line in lines)


def count_matches(haystack: str, needle: str) -> int:
    if not needle:
        return 0
    return haystack.count(needle)


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, max(0, index)) + 1


def apply_edit(source: str, old: str, new: str, *, expect: int = 1) -> EditResult:
    """Replace `old` with `new`, refusing anything ambiguous.

    `expect` is how many occurrences the caller says there are. It defaults to
    1 and a caller that genuinely means "every one of them" has to say so — the
    default cannot be "all", because a model that under-specified a snippet
    would then rewrite every similar line in the file and the diff would look
    deliberate.

    Raises :class:`EditError` for every refusal, each with a sentence naming
    what to do about it, because the recipient is a model that will try again.
    """
    if not old:
        raise EditError("the text to replace is empty; say which text to change")
    if old == new:
        raise EditError("the old and new text are identical; nothing to do")

    hits = count_matches(source, old)
    if hits == expect:
        if expect == 1:
            at = source.index(old)
            return EditResult(source.replace(old, new, 1), EXACT, line_of(source, at))
        return EditResult(source.replace(old, new), EXACT, line_of(source, source.index(old)))

    if hits > expect:
        raise EditError(
            f"that text appears {hits} times, not {expect}. Include more of the "
            "surrounding lines so it is unique, or say how many to replace."
        )

    # Fewer than expected, as written. Try the whitespace ladder before giving
    # up: a model quoting a snippet back rarely reproduces tabs and trailing
    # spaces.
    #
    # The exact rung is tried FIRST and wins outright when it is unique, even
    # if a whitespace-variant of the same text exists elsewhere. Refusing then
    # would break correct edits in every file with mixed indentation, and a
    # literal unique match is the most predictable rule available.
    flat_source = _normalise(source)
    flat_old = _normalise(old)
    flat_hits = count_matches(flat_source, flat_old)
    if flat_hits == 0:
        raise EditError(
            "that text is not in the file. Read it again — it may have changed, "
            "or the quote may be from somewhere else."
        )
    if expect != 1:
        # The loose rung places ONE match, on line boundaries. Letting an
        # `expect` of three through here would replace one and report success,
        # which is the silent under-edit this whole module exists to prevent.
        raise EditError(
            f"that text appears {hits} times as written and {flat_hits} times "
            "once whitespace is ignored. Quote it exactly to replace several."
        )
    if flat_hits > 1:
        raise EditError(
            f"that text appears {flat_hits} times once whitespace is ignored. "
            "Include more of the surrounding lines so it is unique."
        )

    # Exactly the expected number, but only after normalising. Map back to the
    # real source by walking the original lines: the replacement has to go into
    # the file as it actually is, not into the flattened copy.
    replaced = _replace_normalised(source, old, new)
    if replaced is None:
        raise EditError(
            "that text matches only if whitespace is ignored, and the match "
            "could not be placed back into the file. Quote it exactly."
        )
    text, at = replaced
    return EditResult(text, WHITESPACE, line_of(source, at))


def _replace_normalised(source: str, old: str, new: str) -> tuple[str, int] | None:
    """Find `old` in `source` ignoring whitespace differences, and replace it.

    Works line-wise rather than character-wise so the replacement lands on
    whole lines — which is what an edit to source code always is, and which
    means the surrounding indentation of the file is left exactly as it was.
    """
    src_lines = source.replace("\r\n", "\n").split("\n")
    old_lines = _normalise(old).split("\n")
    if not old_lines:
        return None
    flat = [line.replace("\t", "    ").rstrip() for line in src_lines]

    span = len(old_lines)
    for start in range(0, len(flat) - span + 1):
        if flat[start : start + span] != old_lines:
            continue
        # Splice on line boundaries. Rebuilding the whole list rather than
        # doing string surgery is deliberate: an off-by-one in a `find`-based
        # version eats a newline, and the file is one line shorter with no
        # error anywhere.
        rebuilt = src_lines[:start] + new.split("\n") + src_lines[start + span :]
        before = "\n".join(src_lines[:start])
        at = len(before) + (1 if start else 0)
        return "\n".join(rebuilt), at
    return None


def numbered(text: str, *, start: int = 1, limit: int = 0) -> str:
    """`cat -n` output, which is what makes a model able to talk about a place.

    Line numbers are not decoration here: without them a model asked to change
    "the second loop" has no way to say which one it means, and the caller has
    no way to check that the edit landed where it said.
    """
    lines = text.split("\n")
    if limit:
        lines = lines[:limit]
    width = len(str(start + len(lines) - 1))
    return "\n".join(f"{str(start + i).rjust(width)}\t{line}" for i, line in enumerate(lines))


@dataclass
class Hit:
    path: str
    line: int
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "text": self.text}


def search_text(path: str, source: str, pattern: str, *, limit: int = 40) -> list[Hit]:
    """Regex search over one file's text, returning line numbers.

    A bad pattern is a refusal rather than a crash: the pattern came from a
    model, and `(` is a thing models write.
    """
    try:
        matcher = re.compile(pattern)
    except re.error as err:
        raise EditError(f"that is not a valid regular expression: {err}") from err
    out: list[Hit] = []
    for number, line in enumerate(source.split("\n"), 1):
        if matcher.search(line):
            out.append(Hit(path=path, line=number, text=line[:300]))
            if len(out) >= limit:
                break
    return out
