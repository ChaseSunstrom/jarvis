"""Deciding, before the model is called, whether this turn needs working out.

## The problem on both sides

Reasoning is off by default and that is right: the persona is two sentences of
dry wit, the tool loop does the real work, and on a spoken turn a paragraph of
deliberation is silence the user sits through. Half this project's latency
work exists to make "turn the kitchen light off" answer immediately.

But "off" is the wrong answer for the minority of turns that genuinely need
working out — three dependent steps, a condition, a comparison, an ambiguity
that has to be resolved before anything is touched. Those turns get a model
that starts acting on the first clause and discovers the third one halfway
through.

`think_it_through` already lets the model ask. The trouble with asking is that
the model has to notice, and the turns that most need working out are exactly
the ones where it dives in instead. So this decides too — before the first
model call, in code.

## Why this is a heuristic and not a classifier call

Because a classifier call is a round trip, and a round trip on every turn is
the cost this is supposed to avoid paying. The whole thing has to run in
microseconds on the string the user just said, or it is worse than nothing.

## The bias, stated plainly

**It would rather miss a complicated turn than slow a simple one.** A false
positive costs every trivial request a reasoning block; a false negative costs
one complicated request the quality it would have had, and the model can still
escalate for itself. So the thresholds are set high, single commands are
actively pushed down, and anything short with no signal short-circuits to
zero before a single regex runs.

## What it is not

English-only, and pattern-based. It reads the shape of a request, not its
meaning: "sort out the thing" is complicated and scores nothing, and there is
no honest way to fix that from a regex. It is a cheap prior, not a judgement,
and everything downstream treats it as one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "Assessment",
    "STRONG",
    "WEAK",
    "assess",
    "DELIBERATE_AT",
    "DELIBERATE_NOTE",
    "MAX_ASSESS_CHARS",
]

#: The score at which a turn is worth thinking about.
#:
#: Set so that **one STRONG signal is enough and one weak one is not**. A
#: sequence, a condition, a judgement, a typed-out list, or two capability
#: families in one sentence each mean the turn has real structure in it,
#: whatever else is true. A scope word or an exception on its own does not —
#: "switch everything off" is one call.
DELIBERATE_AT = 3

#: A signal that settles it by itself.
STRONG = 3
#: A signal that only counts alongside another.
WEAK = 1

#: Scanning stops here. A wall of pasted text is itself a strong signal, so
#: nothing is lost by not reading all of it, and an unbounded regex sweep on
#: every turn is a latency bug waiting for somebody to paste a book.
MAX_ASSESS_CHARS = 4000

#: How little text is worth reading a shape from.
#:
#: Not purely a fast path — it changes answers, and the trade is deliberate.
#: A three-word turn can be a real judgement ("compare these two") and this
#: will miss it, because three words carry no context and the assessor only
#: ever sees the current turn. Missing that costs one escalation the model can
#: still make for itself. Set any higher and it starts swallowing four-word
#: questions like "why is it dark", which the shape genuinely does identify.
TRIVIAL_WORDS = 3


def _any(*words: str) -> re.Pattern[str]:
    """A word-boundary alternation, compiled once at import."""
    return re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)


#: One thing after another. The strongest single signal there is: a sequenced
#: request is multi-step by construction, whatever it is about.
SEQUENCING = _any(
    r"and then", r"then", r"after that", r"afterwards", r"once you(?:'ve)?",
    r"once that", r"before you", r"finally", r"first(?:ly)?", r"secondly",
    r"lastly", r"followed by", r"and after",
)

#: A branch. Something has to be established before anything can be done.
CONDITIONAL = _any(
    r"if", r"unless", r"in case", r"depending on", r"whether", r"otherwise",
    r"as long as", r"provided that", r"in the event",
)

#: …except when "if" is manners. "Turn the lights off if you would" has no
#: condition in it, and reading one there would slow down every polite request
#: in the house — which is most of them.
POLITENESS = _any(
    r"if you would", r"if you could", r"if you can", r"if you don'?t mind",
    r"if that'?s ok(?:ay)?", r"if possible", r"if you please", r"if you like",
    r"if you wouldn'?t mind",
)

#: A judgement rather than an action. These are the turns where diving in
#: produces a confident wrong answer rather than a broken one.
WEIGHING = _any(
    r"compare", r"comparison", r"which is better", r"which one should",
    r"should i", r"pros and cons", r"trade-?offs?", r"worth it",
    r"recommend", r"best way", r"figure out", r"work out", r"how come",
    r"why (?:is|are|does|do|did|would|should)", r"explain why",
    r"what would happen", r"make sense",
)

#: Everything of a kind, which is rarely one call and often needs a read
#: before the write.
SCOPE = _any(r"all", r"every", r"each", r"everything", r"everywhere", r"any of")

#: A constraint bolted onto the request. "…but not the hall" is a second
#: requirement wearing a conjunction.
CONSTRAINED = _any(
    r"but not", r"except", r"instead of", r"rather than", r"as well as",
    r"apart from", r"other than", r"without",
)

#: Numbers plus something to do with them.
ARITHMETIC = _any(
    r"total", r"average", r"how many", r"how much", r"divide", r"per",
    r"percent", r"cheaper", r"difference between", r"add up",
    r"sum", r"work(?:s)? out to",
)
NUMBER = re.compile(r"\d")

#: Three or more clause breaks. A compound request phrased as one sentence is
#: still a compound request, and this is the only thing that catches the
#: paragraph that has no keyword in it anywhere.
CLAUSE_BREAK = re.compile(r"[,;:]")
MANY_CLAUSES = 3

#: An explicit list the user typed out.
ENUMERATED = re.compile(r"(?:^|\n)\s*(?:[-*•]|\d+[.)])\s+\S", re.MULTILINE)

# There is deliberately NO "starts with a device verb, so push it down" rule.
#
# There was one, and it turned out to change no outcome that the politeness
# rule above did not already handle — while being able to change exactly one:
# "turn the heating down if it is warm", which is a genuine condition on a
# short command and should be settled before the heating moves. A guard that
# never helps and can only produce false negatives is worse than no guard.

#: Nothing to think about.
SOCIAL = re.compile(
    r"^\s*(?:hi|hey|hello|yo|thanks|thank you|ta|cheers|ok|okay|yes|yep|no|"
    r"nope|nevermind|never mind|good morning|good evening|goodnight|"
    r"good night|bye|goodbye|stop|cancel|nice one|great)\b[\s!.,?]*$",
    re.IGNORECASE,
)

#: Vocabulary per capability family. A request that reaches into two of them
#: has an integration problem in it — "email me when the back door opens" is
#: the house AND the outside world, and the join is the hard part.
FAMILIES: dict[str, re.Pattern[str]] = {
    "the house": _any(
        r"lights?", r"lamps?", r"heating", r"thermostat", r"doors?", r"locks?",
        r"blinds?", r"curtains?", r"sensors?", r"switch(?:es)?", r"scene",
        r"automation", r"kitchen", r"bedroom", r"hallway", r"garage",
    ),
    "an outside service": _any(
        r"e-?mail", r"gmail", r"calendar", r"spreadsheets?", r"notion",
        r"slack", r"invoices?", r"drive", r"dropbox", r"sheets?", r"n8n",
        r"webhook", r"api", r"stripe", r"telegram", r"whatsapp",
    ),
    "code": _any(
        r"repo(?:sitory)?", r"branch", r"commit", r"refactor", r"bug",
        r"script", r"function", r"test suite", r"pull request", r"codebase",
    ),
    "the web": _any(
        r"search", r"look up", r"research", r"article", r"website", r"news",
        r"documentation", r"docs", r"price",
    ),
    "a schedule": _any(
        r"every (?:day|morning|evening|night|week|hour|monday|tuesday|"
        r"wednesday|thursday|friday|saturday|sunday)", r"daily", r"weekly",
        r"remind me", r"at \d", r"schedule", r"recurring", r"each morning",
    ),
}


@dataclass(frozen=True)
class Assessment:
    """What the shape of one turn suggests, and why."""

    score: int = 0
    #: Human-readable, in the order they were found. Goes in the log and in
    #: the note handed to the model, so "why is it being slow" has an answer.
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def deliberate(self) -> bool:
        return self.score >= DELIBERATE_AT

    @property
    def why(self) -> str:
        """The reasons as a phrase, or "" when there are none."""
        if not self.reasons:
            return ""
        if len(self.reasons) == 1:
            return self.reasons[0]
        return f"{', '.join(self.reasons[:-1])} and {self.reasons[-1]}"

    def note(self) -> str:
        """The instruction added to a turn that is going to be thought about.

        Deliberately ends by telling the model NOT to narrate the plan. The
        failure this replaces is a model that dives in; the failure it must
        not create is a model that answers "here is my plan" to somebody who
        asked for their lights off.
        """
        if not self.deliberate:
            return ""
        return DELIBERATE_NOTE.format(why=self.why or "several parts")

    def as_dict(self) -> dict[str, object]:
        return {"score": self.score, "reasons": list(self.reasons), "deliberate": self.deliberate}


DELIBERATE_NOTE = (
    "Before you act on this: it has {why}. Work out what has to happen and in "
    "what order, decide what depends on what, and check anything you are "
    "unsure of with a read-only tool rather than assuming it. Resolve any "
    "ambiguity BEFORE the first action that changes something — an action you "
    "have taken cannot be un-decided. Then carry it out. Do not read the plan "
    "out to the user unless they asked for one; do the work and report what "
    "you did."
)


def assess(text: str) -> Assessment:
    """Score one turn. Pure, fast, and wrong sometimes on purpose."""
    said = str(text or "")[:MAX_ASSESS_CHARS].strip()
    if not said:
        return Assessment()

    words = said.split()
    # Two short-circuits before any regex work, because these are most turns.
    if SOCIAL.match(said):
        return Assessment()
    if len(words) <= TRIVIAL_WORDS:
        return Assessment()

    score = 0
    reasons: list[str] = []

    def add(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)

    if SEQUENCING.search(said):
        add(STRONG, "steps that follow one another")
    # Politeness stripped before the test, not excluded after it: "if you
    # would" and a real condition can both be in one sentence.
    if CONDITIONAL.search(POLITENESS.sub("", said)):
        add(STRONG, "a condition to settle first")
    if WEIGHING.search(said):
        add(STRONG, "a judgement to make rather than an action to take")
    if ENUMERATED.search(said):
        add(STRONG, "a list of things to do")

    families = [name for name, pattern in FAMILIES.items() if pattern.search(said)]
    if len(families) >= 2:
        add(STRONG, f"{families[0]} and {families[1]} to join up")

    if SCOPE.search(said):
        add(WEAK, "a scope that has to be read before it is changed")
    if CONSTRAINED.search(said):
        add(WEAK, "an exception to honour")
    if ARITHMETIC.search(said) and NUMBER.search(said):
        add(WEAK, "numbers to work through")
    if len(CLAUSE_BREAK.findall(said)) >= MANY_CLAUSES:
        add(WEAK, "several clauses in one breath")

    if len(words) > 70:
        # Seventy words is not a command. Worth settling on its own, because
        # this is the only thing that catches a long request whose complexity
        # is in its content rather than in any keyword.
        add(STRONG, "a lot to hold at once")
    elif len(words) > 35:
        add(WEAK, "more than a sentence of detail")

    return Assessment(score=score, reasons=tuple(reasons))
