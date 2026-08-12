#!/usr/bin/env python3
"""Executable spec: a question Jarvis asks has to reach the person it asked.

Four defects, one report:

    "when Jarvis asks for approval or a question, the popup doesn't auto come
     up, and I have to manually click on the tool call for it to work, and it
     closes the hey Jarvis popup when I do that, instead of persisting the
     conversation, fix that. also, I should be able to determine whether or not
     Jarvis can do certain tasks or whatever without approval, and it should be
     able to ask me questions over voice"

## 1. The conversation was destroyed by its own prompts

`JarvisAssistActivity` carried `android:noHistory="true"`. That finishes an
activity the moment it stops being visible, and it cannot tell why. The consent
prompt, a question and the permission trampoline all appear over it in tasks of
their own — so every one of them destroyed the conversation on the way up, and
answering returned to nothing. A turn needing one approval could not be
completed at all.

`PermissionRequestActivity` carries the same warning already, in its own words:
"a `noHistory` host is finished the moment it loses the foreground and the
result lands on a dead window".

## 2. Nothing could tell whether the prompt appeared

`startActivity` returning proves nothing: a background activity start the
platform refuses does not throw, it logs and drops the intent. Neither does the
full-screen intent rescue it in the case that matters — while the screen is on
and unlocked the platform deliberately renders a full-screen intent as an
ordinary heads-up notification rather than taking the screen over. So on a
phone in use, with no "display over other apps" grant, nothing raises the
prompt by itself, which is exactly the reported symptom. It is one grant away
from working and the app never said so.

## 3. The policy store had no writer

`PolicyStore.setPolicy` / `clearPolicy` / `all()` / `clearAllPolicies()` are
documented "for the settings UI" and had no caller outside the unit tests, for
the life of the app. The user could not say which actions run without approval.

## 4. A question was silent

`CompanionAskActivity` spoke `speak` messages and not `ask` ones, so Jarvis
asking a question produced a card to be read — on a phone that may be in a
pocket or a car, which is where a voice assistant asking something is most
useful.

Run:  python3 android-app/tools/prompt_reaches_the_user_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "src" / "main"
KOTLIN = MAIN / "kotlin" / "ai" / "jarvis" / "app"
MANIFEST = MAIN / "AndroidManifest.xml"

ASSIST = KOTLIN / "JarvisAssistActivity.kt"
APPROVAL_BRIDGE = KOTLIN / "ui" / "ApprovalBridge.kt"
APPROVAL_ACTIVITY = KOTLIN / "ApprovalActivity.kt"
POLICY_STORE = KOTLIN / "automation" / "policy" / "PolicyStore.kt"
POLICY_SCREEN = KOTLIN / "automation" / "ui" / "ActionPolicyActivity.kt"
ASK = KOTLIN / "companion" / "CompanionAskActivity.kt"
OVERLAY = KOTLIN / "assist" / "AssistOverlay.kt"
WAKE = KOTLIN / "assist" / "WakeWordService.kt"
PRESENCE = KOTLIN / "ui" / "PromptPresence.kt"

#: The two surfaces that host a live conversation, and therefore the two that a
#: prompt appears over. Getting this list wrong is the bug: the first fix for
#: "the prompt closes the overlay" was applied to the ACTIVITY, and the wake
#: word — which is what people actually use — shows the OVERLAY.
CONVERSATION_SURFACES = {
    "JarvisAssistActivity.kt": "the assist-gesture card (an Activity)",
    "WakeWordService.kt": "the wake-word orb (a TYPE_APPLICATION_OVERLAY window)",
}


def read(path: Path) -> str:
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def code_of(path: Path) -> str:
    """Kotlin with comments stripped.

    A comment explaining why a call must be there satisfies a naive search for
    that call — `orb_is_started_test` shipped a draft that passed while the fix
    was reverted, for exactly this reason.
    """
    src = read(path)
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def activity_block(name: str) -> str:
    """The `<activity>` element for [name], attributes only."""
    xml = read(MANIFEST)
    xml = re.sub(r"<!--.*?-->", " ", xml, flags=re.S)
    marker = f'android:name="{name}"'
    at = xml.find(marker)
    assert at >= 0, f"{name} is not declared in the manifest"
    start = xml.rfind("<activity", 0, at)
    end = xml.find(">", at)
    return xml[start:end]


# ---------------------------------------------------------------------------
# 1. the conversation survives its own prompts
# ---------------------------------------------------------------------------
def test_the_assist_surface_is_not_no_history() -> None:
    block = activity_block(".JarvisAssistActivity")
    assert "noHistory" not in block, (
        "the Hey Jarvis surface is android:noHistory again, so every consent "
        "prompt, question and permission dialog destroys the conversation "
        "underneath it and answering returns to nothing"
    )


def test_it_closes_itself_when_the_user_actually_leaves() -> None:
    """Dropping `noHistory` without replacing what it did would leave an
    invisible conversation alive after the user pressed home."""
    src = code_of(ASSIST)
    assert "override fun onStop()" in src, "nothing closes the card any more"
    assert "ourOwnPromptIsUp()" in src, (
        "onStop cannot tell a prompt of ours from the user walking away, so it "
        "either kills the conversation again or never closes"
    )
    for bridge in ("ApprovalBridge.anyPending", "PermissionBridge.anyPending"):
        assert bridge in src, f"{bridge} is not consulted"
    assert "ledger.inFlightCount" in src, "an in-flight question is not counted"
    # ...and a prompt that never comes back must not pin it open for ever.
    assert "GIVE_UP_SLACK_MS" in src and "postDelayed(giveUp" in src, (
        "a crashed or swiped-away prompt leaves the conversation alive for ever"
    )


def test_the_conversation_is_put_down_while_the_prompt_is_up() -> None:
    """A live AudioRecord behind a full-screen consent prompt is recording a
    room whose owner believes the conversation is paused.

    Under `noHistory` this activity was destroyed here and the microphone went
    with it. Now it can sit stopped for over a minute, so it has to give the
    microphone up explicitly — and `holdForQuestion` is exactly that call,
    written for a question taking the mic and saying why in its own words:
    "Give the microphone up completely rather than muting it. Two owners of one
    AudioRecord is the coin toss this whole area exists to avoid."

    `running` stays true through it, deliberately, so no inactivity timer pulls
    the surface out from under a user who is reading a prompt.
    """
    src = code_of(ASSIST)
    stop = src.index("override fun onStop()")
    start = src.index("override fun onStart()")
    assert "holdForQuestion()" in src[stop:stop + 1400], (
        "the conversation keeps its microphone open behind the prompt, so the "
        "VAD and the inactivity timer run against audio nobody meant to send"
    )
    assert "resumeAfterQuestion()" in src[start:start + 900], (
        "the conversation never gets its microphone back, so answering the "
        "prompt returns to a surface that cannot hear"
    )
    # Held, not assumed: `holdForQuestion` returns false when there was nothing
    # to hold, and resuming something that was never held restarts a mic the
    # conversation had deliberately stopped.
    assert "held = convo?.holdForQuestion()" in src, (
        "the resume is unconditional, so it can take a microphone back that was "
        "never given up"
    )


def test_the_bridges_can_answer_that_question() -> None:
    for path in (APPROVAL_BRIDGE, KOTLIN / "ui" / "PermissionBridge.kt"):
        assert "val anyPending: Boolean" in code_of(path), (
            f"{path.name} cannot say whether anything is waiting"
        )


def test_both_conversation_surfaces_survive_a_prompt() -> None:
    """THE MISTAKE THIS FILE MADE ONCE, WRITTEN DOWN.

    There are two "Hey Jarvis" surfaces and only one is an Activity:

      * `JarvisAssistActivity` — the assist gesture. Gets `onStop` when a
        full-screen prompt covers it, and holds the conversation from there.
      * `AssistOverlay` — a `TYPE_APPLICATION_OVERLAY` window put up by
        `WakeWordService`. This is what the WAKE WORD shows, which is to say
        the one people actually see.

    An overlay window has no activity lifecycle at all. The `noHistory` fix and
    the `onStop`/`onStart` hold were correct for the activity and could not
    possibly have applied to the overlay, and the report came back unchanged:
    *"the prompt and such with the siri orb overlay, is still closing the
    overlay when I try to approve permissions"*.

    What tore it down there was a TIMER. `JarvisConversation` runs an 8-second
    inactivity timer; somebody reading a consent prompt says nothing; `onIdle`
    fires and `endOverlayConversation` takes the orb away. `holdForQuestion`
    stops exactly that and had two callers, both for questions.

    So this checks BOTH surfaces, by name, and fails if a third one appears
    without holding.
    """
    for name, what in CONVERSATION_SURFACES.items():
        path = ASSIST if name == "JarvisAssistActivity.kt" else WAKE
        src = code_of(path)
        assert "holdForQuestion()" in src, (
            f"{name} ({what}) never holds its conversation, so the inactivity "
            "timer tears the surface down while the user answers a prompt"
        )
        assert "resumeAfterQuestion()" in src, (
            f"{name} ({what}) never resumes, so answering leaves a surface that "
            "cannot hear"
        )


def test_the_overlay_gets_off_the_prompts_buttons() -> None:
    """An overlay window is drawn ABOVE every Activity.

    The orb is a 340dp card anchored 72dp off the bottom; `ApprovalActivity`
    puts DENY and APPROVE at the end of its column. `FLAG_NOT_TOUCH_MODAL`
    passes through only the touches that land OUTSIDE the card — so the buttons
    were on screen and unpressable, and the notification was the only way
    through. That is *"it still forces me to click on the tool call to
    approve"*, and it is not a background-activity-start problem at all: the
    prompt was there the whole time.
    """
    overlay = code_of(OVERLAY)
    assert "fun setHiddenForPrompt(" in overlay, (
        "the overlay cannot be moved out of a prompt's way"
    )
    # The function's own body, cut at the next declaration. A 400-char window
    # ran straight into `fun detach()` below it and failed on its own name.
    after = overlay[overlay.index("fun setHiddenForPrompt("):]
    body = after[: after.index("\n    fun ", 1)]
    assert "View.GONE" in body, "hidden by something other than visibility"
    assert "detach" not in body, (
        "hiding by detaching drops the view tree and the conversation's "
        "callbacks with it; the orb has to come back when the prompt is answered"
    )
    assert "setHiddenForPrompt(true)" in code_of(WAKE), "nothing ever hides it"
    assert "setHiddenForPrompt(false)" in code_of(WAKE), "nothing ever brings it back"


def test_a_prompt_announces_itself_to_the_surfaces() -> None:
    """Both bridges, both edges, and the settle in a `finally`.

    A leaked `raised()` leaves the orb hidden and the conversation held for the
    life of the process — worse than the bug it fixes — so the pairing is
    checked rather than assumed.
    """
    presence = code_of(PRESENCE)
    assert "fun raised()" in presence and "fun settled()" in presence
    for path in (APPROVAL_BRIDGE, KOTLIN / "ui" / "PermissionBridge.kt"):
        src = code_of(path)
        assert "PromptPresence.raised()" in src, f"{path.name} announces nothing"
        settled = src.index("PromptPresence.settled()")
        finally_at = src.rindex("} finally {", 0, settled)
        assert finally_at >= 0, f"{path.name} settles outside a finally"
    # ...and the count floors at zero, so an unpaired settle cannot swallow the
    # next prompt's raise.
    assert "if (it > 0) it - 1 else 0" in presence, (
        "the prompt count can go negative, which silently disables the next one"
    )


# ---------------------------------------------------------------------------
# 2. the app knows whether the prompt appeared
# ---------------------------------------------------------------------------
def test_the_consent_prompt_reports_that_it_reached_the_screen() -> None:
    bridge = code_of(APPROVAL_BRIDGE)
    assert "fun raised(" in bridge, "the bridge has no way to be told"
    assert "ApprovalBridge.raised(" in code_of(APPROVAL_ACTIVITY), (
        "the prompt never reports itself, so `startActivity` returning — which "
        "proves nothing — is again the only evidence the app has"
    )


def test_a_prompt_that_never_appeared_is_explained_once() -> None:
    bridge = code_of(APPROVAL_BRIDGE)
    assert "RAISE_GRACE_MS" in bridge
    assert "explainWhyNothingAppeared(" in bridge, (
        "a prompt that could not raise itself is silently left as a "
        "notification to be tapped, for ever, with nothing saying why"
    )
    assert "Settings.ACTION_MANAGE_OVERLAY_PERMISSION" in bridge, (
        "the explanation does not carry the one-tap fix"
    )
    assert "compareAndSet(false, true)" in bridge, (
        "a run of gated commands becomes a run of identical complaints"
    )
    # The wait for confirmation must be far shorter than the wait for an
    # answer, or it delays the prompt rather than diagnosing it.
    grace = int(re.search(r"RAISE_GRACE_MS = ([\d_]+)L", bridge).group(1).replace("_", ""))
    timeout = int(re.search(r"TIMEOUT_MS = ([\d_]+)L", bridge).group(1).replace("_", ""))
    assert grace < timeout / 4, f"{grace}ms is not meaningfully shorter than {timeout}ms"


def test_the_confirmation_slot_exists_before_anything_can_start_the_activity() -> None:
    """`startActivity` is asynchronous but not slow. A prompt that reported
    itself before the map had an entry for it would look exactly like one that
    never appeared."""
    bridge = code_of(APPROVAL_BRIDGE)
    registered = bridge.index("onScreen[id] = CompletableDeferred()")
    raised = bridge.index("raisePrompt(")
    assert registered < raised, (
        "the on-screen slot is created after the prompt can already be running"
    )


# ---------------------------------------------------------------------------
# 3. the user decides what runs without asking
# ---------------------------------------------------------------------------
def test_something_writes_the_policy_store() -> None:
    screen = code_of(POLICY_SCREEN)
    for call in ("setPolicy(", "clearPolicy(", "clearAllPolicies()", ".all()"):
        assert call in screen, f"the policy screen never calls {call}"


def test_the_screen_is_declared_and_reachable() -> None:
    block = activity_block("ai.jarvis.app.automation.ui.ActionPolicyActivity")
    assert 'android:exported="false"' in block, (
        "the policy screen is exported; another app could open the control "
        "that decides what Jarvis does without asking"
    )
    screens = code_of(KOTLIN / "ui" / "JarvisScreens.kt")
    assert "ActionPolicyActivity" in screens, "no launcher constant"
    assert "JarvisScreens.ACTION_POLICY" in code_of(KOTLIN / "SettingsActivity.kt"), (
        "nothing in Settings opens it, so it is a screen with no door"
    )


def test_always_is_not_offered_for_a_tier_three_action() -> None:
    """`PolicyStore.setPolicy` refuses it and `PolicyEngine.decide` ignores it,
    so this is the third of three independent guards — but a control that is
    offered and then silently ignored is worse than one that was never there."""
    screen = code_of(POLICY_SCREEN)
    assert "tier != ActionTier.CONFIRM" in screen, (
        "ALLOW_ALWAYS is offered for every tier"
    )
    # NEVER, by contrast, is available everywhere: tightening must always work.
    assert "add(UserPolicy.NEVER)" in screen
    assert screen.index("add(UserPolicy.NEVER)") > screen.index("if (allowAlways)"), (
        "NEVER is inside the tier condition, so the most dangerous actions "
        "cannot be turned off"
    )
    # And the refusal is the store's answer, not this screen's guess at it.
    assert "if (!live.setPolicy(" in screen, (
        "the screen assumes its write took; PolicyStore.setPolicy returns false "
        "when it refuses and that has to be shown"
    )


# ---------------------------------------------------------------------------
# 4. the question has a voice
# ---------------------------------------------------------------------------
def test_a_question_is_read_aloud() -> None:
    src = code_of(ASK)
    assert "fun askAloud(" in src, "an `ask` message is still silent"
    ask_branch = src[src.index("MODE_ASK ->"):][:600]
    assert "askAloud()" in ask_branch, "the ask branch does not speak"


def test_it_will_not_say_out_loud_what_it_would_not_print() -> None:
    """Speaking a question is a strictly louder version of putting it on the
    lock screen. CompanionAskGate is already the authority on that, and it has
    to stay the only one."""
    src = code_of(ASK)
    body = src[src.index("fun askAloud("):][:1600]
    assert "CompanionAskGate.textVisible(isLocked(), importance)" in body, (
        "the question is read aloud without asking whether it may be shown, so "
        "a `critical` message names a person to whoever is holding the phone"
    )
    assert "spokeQuestion" in body, "unlocking re-reads the question from the top"


def test_answering_does_not_need_a_second_tap() -> None:
    src = code_of(ASK)
    body = src[src.index("fun askAloud("):][:2200]
    assert "toggleListening()" in body, "the mic does not open after the question"
    assert "CompanionAskGate.answerEnabled(" in body, (
        "the mic opens without going through the same gate as the button"
    )
    assert "PackageManager.PERMISSION_GRANTED" in body, (
        "auto-listening does not check RECORD_AUDIO, so it raises a permission "
        "dialog nobody asked for on top of a question"
    )


def test_a_question_with_options_is_not_answered_by_voice() -> None:
    """A question carrying options is answered by tapping one, and the answer
    must be one of those exact strings. Opening the microphone over the top of
    that offers a reply the caller cannot accept, and moves the screen under a
    user already reaching for a button.

    It also stopped `CompanionAskTest` answering at all — the same fact seen
    from the other side, and the reason this check exists rather than a note.
    """
    src = code_of(ASK)
    body = src[src.index("fun askAloud("):][:2200]
    assert "options.isEmpty()" in body, (
        "the microphone opens over a question that has buttons"
    )
    assert body.index("options.isEmpty()") < body.index("toggleListening()"), (
        "the options check does not guard the microphone"
    )


def main() -> int:
    tests = [
        (n, f) for n, f in sorted(globals().items())
        if n.startswith("test_") and callable(f)
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
