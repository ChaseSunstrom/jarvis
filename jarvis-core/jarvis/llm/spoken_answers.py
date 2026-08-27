"""Does the next thing said resolve the request that is waiting? (M66)

"I should be able to verbally confirm it." While a question or a held action
waits on a conversation, the next turn in that conversation may be its answer
— "the corner one", "yes, go ahead", "cancel" — and the agent asks this module
before it asks the model.

The rules are pinned in ``tests/contracts/spoken_answers.json`` and every case
there runs against :func:`decide`. They are deliberately narrow. A wrong match
here approves an action the person did not confirm, so an ACTION is approved
only by a whole utterance that is one of :data:`AFFIRMATIONS`, a QUESTION with
choices only by words that pick out exactly one choice, and nothing at all is
resolved when more than one request waits or when the request was raised
after untrusted content was read. Everything this module declines to decide
goes to the model as an ordinary turn, with a note that something waits — the
model can ask again, which is always cheaper than being wrong.

What this does **not** cover: it never looks at the request's arguments, so a
choice answer is the choice's own text and a free-text answer is the words as
said; `approve_request` decides which single argument, if any, an answer may
write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: Whole utterances (normalised) that approve the one action waiting.
AFFIRMATIONS: frozenset[str] = frozenset(
    {
        "yes", "yes please", "yep", "yeah", "yup", "ok", "okay", "sure", "sure thing",
        "go ahead", "go for it", "do it", "please do", "confirm", "confirmed", "approve",
        "approved", "proceed", "affirmative", "yes go ahead", "yes do it", "ok go ahead",
        "okay go ahead", "ok do it", "okay do it", "yes confirm", "that's fine", "fine",
        "carry on", "yes carry on",
    }
)

#: Whole utterances (normalised) that deny the one action waiting, or dismiss
#: the one question.
DENIALS: frozenset[str] = frozenset(
    {
        "no", "nope", "nah", "cancel", "cancel that", "cancel it", "don't", "do not",
        "don't do it", "do not do it", "no don't", "no cancel", "stop", "no thanks",
        "no thank you", "never mind", "nevermind", "forget it", "deny", "denied", "abort",
        "leave it", "not now", "no leave it",
    }
)

#: Words that carry nothing when matching an utterance against a choice.
STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "one", "ones", "that", "this", "it", "i", "i'd", "id", "want",
        "would", "like", "with", "option", "choose", "pick", "select", "let's", "lets",
        "go", "for", "of", "them", "those", "these", "my", "me", "to", "yes", "no",
    }
)

#: Removed from either end of an utterance before the lists are consulted.
#: Never from the middle: "yes please turn it on" is not "yes".
#: The forms of address come off too: Whisper hears a short "Yes." as "Yes, sir"
#: (the confirm scenario, 27 Aug 2026), and a person who says "yes, ma'am" to a
#: house that calls them ma'am has said yes.
EDGE_FILLERS: tuple[str, ...] = (
    "jarvis", "please", "thanks", "thank you", "hey", "sir", "ma'am", "madam",
)

_NOT_WORD = re.compile(r"[^0-9a-z\s]+")

#: The kinds a decision can be. `none` is the common one — an ordinary turn.
KIND_APPROVE = "approve"
KIND_DENY = "deny"
KIND_ANSWER = "answer"
KIND_NONE = "none"
KIND_AMBIGUOUS = "ambiguous"
KIND_TAINTED = "tainted"


@dataclass(slots=True)
class Decision:
    kind: str
    #: Which of the pending requests, by position in the list given.
    index: int | None = None
    #: For `answer`: what to hand `approve_request` as the answer.
    answer: str | None = None

    @property
    def resolves(self) -> bool:
        return self.kind in (KIND_APPROVE, KIND_DENY, KIND_ANSWER)


def normalise(text: str) -> str:
    """Lowercase letters and digits, single-spaced, apostrophes dropped, the
    edge fillers gone. The one form both the lists and the utterance take."""
    lowered = str(text or "").lower().replace("'", "").replace("’", "")
    words = _NOT_WORD.sub(" ", lowered).split()
    fillers = [f.replace("'", "").split() for f in EDGE_FILLERS]
    changed = True
    while words and changed:
        changed = False
        for filler in fillers:
            n = len(filler)
            if len(words) > n and words[:n] == filler:
                words = words[n:]
                changed = True
            if len(words) > n and words[-n:] == filler:
                words = words[:-n]
                changed = True
    return " ".join(words)


def _affirmed(said: str) -> bool:
    return said in {normalise(a) for a in AFFIRMATIONS}


#: Words that point back at the request rather than name anything new — and
#: the joins between them. An affirmation followed only by these, the
#: request's own words and stopwords is that affirmation: "yes, close it" to
#: "Shall I close the garage door?" is a yes. Pinned to the contract's
#: `tail_words`.
TAIL_WORDS: frozenset[str] = frozenset(
    {
        "it", "that", "this", "one", "them", "those", "these",
        "do", "go", "ahead", "now", "then", "so", "and",
    }
)


def _request_words(request: Mapping[str, Any]) -> set[str]:
    """The words a request was raised with: its summary and its question."""
    arguments = request.get("arguments") if isinstance(request.get("arguments"), Mapping) else {}
    text = " ".join(
        str(part or "")
        for part in (request.get("summary"), arguments.get("question"), arguments.get("summary"))
    )
    return set(normalise(text).split())


def _opens_with_affirmation(said: str) -> bool:
    words = said.split()
    affirmations = {normalise(a) for a in AFFIRMATIONS}
    return any(" ".join(words[:n]) in affirmations for n in range(1, len(words)))


def _affirmed_with_tail(said: str, request: Mapping[str, Any]) -> bool:
    """An affirmation followed by words that only re-say the request.

    "Yes, close it." said to "Shall I close the garage door?" is a yes; on the
    nineteenth house (27 Aug 2026) it was not, so the model read "close it"
    as a new order and closed the living room window. A tail that names
    anything the request did not — "yes, close the window" — is NOT a yes:
    it is a new instruction, and the request keeps waiting.
    """
    words = said.split()
    affirmations = {normalise(a) for a in AFFIRMATIONS}
    for n in range(len(words) - 1, 0, -1):
        if " ".join(words[:n]) in affirmations:
            tail = words[n:]
            allowed = TAIL_WORDS | STOPWORDS | _request_words(request)
            return all(word in allowed for word in tail)
    return False


def _denied(said: str) -> bool:
    return said in {normalise(d) for d in DENIALS}


def _pick_choice(said: str, choices: Sequence[str]) -> str | None:
    """The one choice the words pick out, or None when they pick none or two."""
    if not said:
        return None
    normalised = [(choice, normalise(choice)) for choice in choices]
    # 1. the whole utterance is a choice, or a choice sits in it as a phrase
    for choice, plain in normalised:
        if plain and (said == plain or f" {plain} " in f" {said} "):
            return choice
    # 2. every content word of the utterance sits inside exactly one choice
    content = [w for w in said.split() if w not in STOPWORDS]
    if not content:
        return None
    fitting = [
        choice
        for choice, plain in normalised
        if plain and all(word in plain.split() for word in content)
    ]
    return fitting[0] if len(fitting) == 1 else None


def decide(pending: Sequence[Mapping[str, Any]], utterance: str) -> Decision:
    """Which pending request, if any, `utterance` resolves, and how.

    `pending` is what `ToolRegistry.pending_for_conversation` returns for the
    conversation the utterance belongs to — dicts with `answerable`, `choices`
    and `tainted`. Nothing here reads the arguments.
    """
    raw = str(utterance or "").strip()
    said = normalise(raw)
    if not pending or not said:
        return Decision(KIND_NONE)

    if len(pending) > 1:
        # A yes cannot be attributed to one of two; anything else is a turn.
        if _affirmed(said) or _denied(said):
            return Decision(KIND_AMBIGUOUS)
        return Decision(KIND_NONE)

    request = pending[0]
    verdict = _decide_one(request, raw, said)
    if verdict.kind != KIND_NONE and request.get("tainted"):
        # The words would have resolved it, and that is exactly the case the
        # banner exists for: it is the surface that says where the question's
        # words came from. Nothing is touched.
        return Decision(KIND_TAINTED, 0)
    return verdict


def _decide_one(request: Mapping[str, Any], raw: str, said: str) -> Decision:
    answerable = request.get("answerable")
    if not answerable:
        if _affirmed(said) or _affirmed_with_tail(said, request):
            return Decision(KIND_APPROVE, 0)
        if _denied(said):
            return Decision(KIND_DENY, 0)
        return Decision(KIND_NONE)

    choices = [str(c) for c in (request.get("choices") or []) if str(c).strip()]
    if not choices:
        # Free text: the words are the answer, as said. A URL, a name, a
        # number — normalising any of them would hand the tool a wrong one.
        if _denied(said):
            return Decision(KIND_DENY, 0)
        return Decision(KIND_ANSWER, 0, raw)

    picked = _pick_choice(said, choices)
    affirmed = _affirmed(said) or _affirmed_with_tail(said, request)
    if picked is not None and said != normalise(picked) and _opens_with_affirmation(said) and not affirmed:
        # "Yes, close the window" contains the choice "yes" as a phrase, and
        # the rest is a new order about something the question never named.
        # The yes is not taken: taking it would close the garage AND leave the
        # model to close the window. A denial with a tail ("no, leave it open")
        # is taken: dismissing runs nothing, whatever the tail says.
        picked = None
    if picked is None and (affirmed or _denied(said)):
        # "yeah" for a choice that is "yes": the choice list is the answer
        # vocabulary, and the lists say which of its entries this word is.
        # Checked before the dismissal below, or "nope" would dismiss a
        # question whose choices were yes and no.
        wanted = affirmed
        fitting = [
            c for c in choices if (_affirmed(normalise(c)) if wanted else _denied(normalise(c)))
        ]
        picked = fitting[0] if len(fitting) == 1 else None
    if picked is not None:
        return Decision(KIND_ANSWER, 0, picked)
    if _denied(said):
        return Decision(KIND_DENY, 0)
    return Decision(KIND_NONE)
