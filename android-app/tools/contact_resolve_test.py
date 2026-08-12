#!/usr/bin/env python3
"""Executable spec: turning "text Mum" into a number, before anyone approves it.

Reported as: *"I asked it to text someone, and it never did, even though it has
correct permissions."*

Nothing was broken in `send_sms`. The path simply had two steps in it and the
planner only ever emitted one. To text a person the model had to call
`read_contacts`, read a number out of an untrusted result, and then call
`send_sms` with it — two device round trips with a Tier-2 prompt and a Tier-3
prompt between the request and the message. An 8B planner drops that, and when
it drops it the fallback is `send_sms(number="Mum")`, which fails the
plausibility check and returns an error the model then narrates around.

So the lookup moved onto the device, into `JarvisAction.resolve`, which runs
BEFORE the policy engine. That placement is the whole design and it is not
negotiable: this project's rule is *what was approved is what runs*, and a
consent prompt reading `to: "Mum"` while the message goes to a number nobody
was shown is a prompt that lied. Resolve first, then show the human the real
number, then send exactly that.

This file mirrors `ContactResolver.resolveTarget` in Python, runs the cases,
and structurally checks the Kotlin still has the properties the model relies on.

Run:  python3 android-app/tools/contact_resolve_test.py
      python3 -m pytest android-app/tools/contact_resolve_test.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

KOTLIN = Path(__file__).resolve().parents[1] / (
    "app/src/main/kotlin/ai/jarvis/app/automation/actions/builtin/CommsActions.kt"
)
ACTION = Path(__file__).resolve().parents[1] / (
    "app/src/main/kotlin/ai/jarvis/app/automation/actions/JarvisAction.kt"
)

TARGET_KEYS = ["number", "to", "contact", "recipient", "name"]
ALLOWED = re.compile(r"^[+0-9 ()\-.]{3,25}$")


def is_plausible(number: str) -> bool:
    """Mirrors PhoneNumbers.isPlausible."""
    if not ALLOWED.match(number):
        return False
    return 3 <= sum(character.isdigit() for character in number) <= 20


def contact_key(number: str) -> str:
    """Mirrors the de-duplication key: the last nine digits."""
    return "".join(c for c in number if c.isdigit())[-9:]


def resolve_target(params: dict, book: list[tuple[str, str]], granted=True) -> tuple:
    """Mirrors ContactResolver.resolveTarget -> (kind, params_or_message)."""
    wanted = next((params[key] for key in TARGET_KEYS if params.get(key)), None)
    if wanted is None:
        return ("unchanged", params)
    if is_plausible(wanted):
        if params.get("number") == wanted:
            return ("unchanged", params)
        return ("resolved", {**params, "number": wanted})
    if not granted:
        return ("failed", "no contacts permission")

    # CONTENT_FILTER_URI matches on a name prefix, per contact.
    matches = [
        (name, number)
        for name, number in book
        if wanted.lower() in name.lower()
    ]
    found: dict[str, tuple[str, str]] = {}
    for name, number in matches:
        found.setdefault(contact_key(number), (number, name))
    if not found:
        return ("failed", f'no contact matches "{wanted}"')
    if len(found) == 1:
        number, name = next(iter(found.values()))
        return ("resolved", {**params, "number": number, "contact": name or wanted})
    return ("failed", f'"{wanted}" matches {len(found)} contacts')


BOOK = [
    ("Mum", "+44 7700 900123"),
    ("Mum", "07700900123"),          # same number, second synced account
    ("Chris Bell", "+44 7700 900555"),
    ("Chris Nolan", "+44 7700 900777"),
    ("Sam", "07700 900999"),
]


# --- the cases --------------------------------------------------------------
def test_a_real_number_is_left_alone():
    kind, out = resolve_target({"number": "+44 7700 900123", "body": "hi"}, BOOK)
    assert kind == "unchanged", out


def test_a_number_under_another_key_is_normalised_onto_number():
    """The model writes `to`, `recipient` or `contact` depending on its mood.
    `execute` reads exactly one key, so resolution is where they converge."""
    for key in ("to", "recipient", "contact", "name"):
        kind, out = resolve_target({key: "+44 7700 900123", "body": "hi"}, BOOK)
        assert kind == "resolved", key
        assert out["number"] == "+44 7700 900123"


def test_a_name_becomes_a_number():
    kind, out = resolve_target({"to": "Mum", "body": "on my way"}, BOOK)
    assert kind == "resolved", out
    assert out["number"] == "+44 7700 900123"
    assert out["contact"] == "Mum"
    # The body is carried through untouched — resolution rewrites the target
    # and nothing else.
    assert out["body"] == "on my way"


def test_one_person_listed_twice_is_not_a_choice():
    """Two synced address books is the normal state of a phone. Counting rows
    instead of distinct numbers would refuse the commonest lookup there is."""
    kind, out = resolve_target({"to": "Mum"}, BOOK)
    assert kind == "resolved", out


def test_two_different_people_is_refused_not_guessed():
    """Picking the alphabetically-first Chris and messaging them is not a
    recovery. The model has `ask_user` for this."""
    kind, message = resolve_target({"to": "Chris"}, BOOK)
    assert kind == "failed"
    assert "2 contacts" in message


def test_an_unknown_name_is_refused_with_something_actionable():
    kind, message = resolve_target({"to": "Rumpelstiltskin"}, BOOK)
    assert kind == "failed"
    assert "no contact matches" in message


def test_no_contacts_permission_is_its_own_answer():
    """Distinct from "no such person": one is fixed in Settings and the other
    is not, and the model should say which."""
    kind, message = resolve_target({"to": "Mum"}, BOOK, granted=False)
    assert kind == "failed"
    assert "permission" in message


def test_a_number_that_is_not_a_number_is_never_silently_dialled():
    for junk in ("Mum's mobile", "the office", "<script>", ""):
        kind, _ = resolve_target({"to": junk} if junk else {}, BOOK)
        assert kind in ("failed", "unchanged"), junk


# --- the Kotlin -------------------------------------------------------------
def test_both_reaching_actions_resolve():
    """`send_sms` and `place_call` are the two that reach a human being."""
    source = KOTLIN.read_text(encoding="utf-8")
    for action in ("SendSms", "PlaceCall"):
        block = source.split(f"object {action} :", 1)
        assert len(block) == 2, f"{action} is gone"
        body = block[1][:2400]
        assert "override suspend fun resolve(" in body, (
            f"{action} no longer resolves its target before the consent prompt"
        )
        assert "ContactResolver.resolveTarget" in body, (
            f"{action} resolves by some other route than the shared resolver"
        )


def test_the_schema_tells_the_model_a_name_is_allowed():
    """The manifest is what the model reads. A schema that still says
    "phone number" is a schema that keeps producing the two-step plan that
    does not complete."""
    source = KOTLIN.read_text(encoding="utf-8")
    for action in ("SendSms", "PlaceCall"):
        body = source.split(f"object {action} :", 1)[1][:1200]
        assert "contact name OR phone number" in body, (
            f"{action}'s paramsSchema no longer advertises that a name works"
        )


def test_execute_reads_only_the_resolved_key():
    """Reading `to` in execute would mean running something other than what
    the human approved, because the prompt was shown the resolved payload."""
    source = KOTLIN.read_text(encoding="utf-8")
    for action in ("SendSms", "PlaceCall"):
        body = source.split(f"object {action} :", 1)[1]
        body = body.split("override suspend fun execute(", 1)[1][:900]
        assert 'params.str("number")' in body, f"{action}.execute lost the number"
        for key in ("to", "contact", "recipient"):
            assert f'params.str("{key}")' not in body, (
                f'{action}.execute reads "{key}" directly, bypassing resolution'
            )


def test_the_resolver_is_documented_as_read_only():
    """It runs before every gate, so a resolver with a side effect is an
    un-gated side effect."""
    source = ACTION.read_text(encoding="utf-8")
    body = source.split("suspend fun resolve(", 1)
    assert len(body) == 2, "JarvisAction.resolve is gone"
    doc = source.split("suspend fun resolve(")[0][-3000:]
    assert "Read-only" in doc, "the read-only constraint is no longer stated"
    assert "cannot lower a tier" in doc, "the tier constraint is no longer stated"


def test_resolution_does_not_mutate_the_caller_s_object():
    """`copyWith` exists so the audit log's "as requested" and the prompt's
    "as resolved" can differ. Mutating in place would collapse them."""
    source = KOTLIN.read_text(encoding="utf-8")
    assert "fun JSONObject.copyWith(" in source, "the non-mutating copy helper is gone"
    body = _function_body(source, "fun resolveTarget(")
    assert "copyWith(" in body, "resolveTarget no longer goes through the copy helper"
    assert ".put(" not in body, (
        "resolveTarget writes into a JSONObject directly; it must build a copy"
    )


def _function_body(source: str, signature: str) -> str:
    """The text of one Kotlin function, by brace matching.

    A fixed-size window was tried first and it read straight past the end of
    `resolveTarget` into `copyWith` — whose whole job is to call `.put`, which
    is the thing the caller must not do. A structural check that fails on the
    helper it is checking for is worse than no check.
    """
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def main() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # a broken check is a failure, not an abort
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
