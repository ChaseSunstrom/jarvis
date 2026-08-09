package ai.jarvis.app.audio

/**
 * PURE LOGIC — no Android imports, no I/O, no clock. Unit-tested on a plain JVM
 * (`MediaButtonGateTest`) and mirrored in `tools/media_button_test.py`.
 *
 * What the button on a headset does. The whole point of an all-day earpiece is
 * that you never take the phone out, so the one physical control it has becomes
 * the primary way to summon Jarvis — which makes it worth being precise about.
 *
 * A headset button is not a trusted input. It is a switch on a small object that
 * may be sitting on a desk, in a bag, paired to a phone across the room, or in
 * someone else's hand. Bluetooth media keys arrive with no indication of who
 * pressed them, and a cheap headset will bounce a single press into three
 * events. So this gate is written as a list of things the button may NOT do:
 *
 *  1. **It may never answer a consent prompt.** While a Tier-3 prompt is
 *     waiting, every press is swallowed — not forwarded to the assistant, not
 *     forwarded to the media player, not counted for the double-press timer.
 *     Approving a payment or a message must cost a deliberate look at a screen
 *     and a tap on it; it must never be reachable by a button someone can press
 *     through a coat pocket. The gate does not even expose an outcome that
 *     could approve something — [Action] has no such value.
 *  2. **It may not silently steal play/pause.** If music is playing and the
 *     user taps the button, they mean pause. Hijacking that would make Jarvis
 *     the reason their podcast will not stop. Summoning Jarvis over playing
 *     media takes a long press, which no media app treats as play/pause.
 *  3. **It may not fire twice for one press.** Contact bounce and duplicated
 *     Bluetooth AVRCP events are normal, so presses inside [DEBOUNCE_MS] of the
 *     last accepted one are dropped.
 *  4. **It does nothing at all when headset mode is off.** The user opting out
 *     means the button belongs to whatever app owned it before Jarvis existed.
 *
 * Note that starting a conversation from the keyguard IS allowed. That is the
 * feature working as intended and it is safe: the assist surface over a locked
 * phone can hear a question and answer it, while `ConsentGate` independently
 * guarantees that nothing requiring approval can be approved until the phone is
 * unlocked. Rule 1 is what keeps those two facts from meeting.
 */
object MediaButtonGate {

    /** Presses closer together than this are contact bounce, not intent. */
    const val DEBOUNCE_MS = 350L

    /** Held at least this long is a long press: "summon Jarvis" over media. */
    const val LONG_PRESS_MS = 600L

    /** What the app should do with a press. */
    enum class Action {
        /** Swallow it. Nothing sees this press. */
        IGNORE,

        /** Hand it to whatever media app would have received it. */
        PASS_TO_MEDIA,

        /** Open the assist surface and start listening. */
        START_TURN,

        /** Stop capturing and let the current turn complete. */
        END_TURN
    }

    /**
     * @param headsetModeEnabled the user's opt-in. False hands the button back.
     * @param consentPending a Tier-3 approval prompt is on screen, waiting.
     * @param inConversation Jarvis is currently mid-turn (listening or speaking).
     * @param musicActive another app holds audio focus and is playing.
     * @param heldMs how long the button was down. >= [LONG_PRESS_MS] is a long
     *   press.
     * @param msSinceLastAccepted milliseconds since the last press this gate
     *   accepted, or [Long.MAX_VALUE] if there has not been one.
     */
    fun decide(
        headsetModeEnabled: Boolean,
        consentPending: Boolean,
        inConversation: Boolean,
        musicActive: Boolean,
        heldMs: Long,
        msSinceLastAccepted: Long
    ): Action {
        // Rule 1, and it is first for a reason: this check must not be reachable
        // around. Not even PASS_TO_MEDIA, because a media app taking focus can
        // pull the prompt out from under the user mid-decision.
        if (consentPending) return Action.IGNORE

        // Rule 4.
        if (!headsetModeEnabled) return Action.PASS_TO_MEDIA

        // Rule 3. Deliberately after the consent check so a bounced press during
        // a prompt is still swallowed rather than merely de-duplicated.
        if (msSinceLastAccepted < DEBOUNCE_MS) return Action.IGNORE

        val longPress = heldMs >= LONG_PRESS_MS

        // Mid-conversation the button is "I'm done talking" — including a long
        // press, which is the natural way to cut Jarvis off.
        if (inConversation) return Action.END_TURN

        // Rule 2.
        if (musicActive && !longPress) return Action.PASS_TO_MEDIA

        return Action.START_TURN
    }

    /**
     * Whether a press with this outcome should reset the debounce clock.
     *
     * Only outcomes Jarvis acted on count. A press handed to a media app is not
     * ours to debounce — double-tap-to-skip is a real gesture in every player,
     * and swallowing the second tap would break it.
     */
    fun resetsDebounce(action: Action): Boolean =
        action == Action.START_TURN || action == Action.END_TURN
}
