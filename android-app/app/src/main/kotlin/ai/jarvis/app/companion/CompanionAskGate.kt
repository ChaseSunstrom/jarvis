package ai.jarvis.app.companion

/**
 * PURE LOGIC — no Android imports, no I/O, no clock.
 *
 * When [CompanionAskActivity] may show the question, and when a control may do
 * anything. The sibling of [ai.jarvis.app.ui.ConsentGate], and deliberately
 * shaped the same way, because the underlying problem is the same: the screen
 * lights up over a keyguard, and a locked phone is a phone in someone else's
 * hand.
 *
 * The differences from the consent gate are the interesting part:
 *
 *  * **The question is hidden only when it is sensitive.** A Tier-3 prompt's
 *    parameters are always hidden because they always name people, numbers and
 *    commands. A companion question is often "the kettle boiled" — hiding that
 *    would make the lock-screen delivery useless. So the importance the server
 *    assigned decides: `high` and `critical` show
 *    [ai.jarvis.app.companion.CompanionAskGate.HIDDEN_TEXT] until the phone is
 *    unlocked, everything else reads through the keyguard.
 *  * **Answering always needs an unlocked phone**, even for a question that is
 *    readable through the keyguard. An answer is data, not an authorisation
 *    token — but it is data the *user* is supposed to author, and a pocket tap
 *    or a stranger is neither.
 *  * **Dismissing is live in every state.** Refusing is safe from a locked
 *    screen, from a stranger and from a stray tap: it reports `dismissed`, the
 *    server escalates, and the question reaches the user somewhere else. The
 *    asymmetry is the point — every uncertain state must be able to say "not
 *    here" and must not be able to say "yes".
 */
object CompanionAskGate {

    /** How long a control stays inert after the question becomes readable. */
    const val ARM_MS = 500L

    /** Stands in for a sensitive question while the keyguard is up. */
    const val HIDDEN_TEXT = "Jarvis has a question."

    /** Shown when the server sent no text at all. */
    const val NO_TEXT = "(no message)"

    /** Sensitive questions are the ones the lock screen must not spell out. */
    fun sensitive(importance: String): Boolean =
        importance.trim().lowercase() in CompanionProtocol.SENSITIVE_IMPORTANCE

    /** May the verbatim question be rendered right now? */
    fun textVisible(locked: Boolean, importance: String): Boolean =
        !locked || !sensitive(importance)

    /** What the question block should read. Never leaks a sensitive question. */
    fun textFor(locked: Boolean, importance: String, text: String): String = when {
        !textVisible(locked, importance) -> HIDDEN_TEXT
        text.isBlank() -> NO_TEXT
        else -> text
    }

    /**
     * May an option button, the mic or the text field do anything?
     *
     * Unlocked, armed, unanswered — any doubt is a no. Note it also requires
     * the question to be visible: answering something you cannot read is not
     * answering.
     */
    fun answerEnabled(
        locked: Boolean,
        armed: Boolean,
        answered: Boolean,
        importance: String = "normal",
    ): Boolean = !locked && armed && !answered && textVisible(locked, importance)

    /** DISMISS is live whenever the question is unanswered, locked or not. */
    fun dismissEnabled(answered: Boolean): Boolean = !answered

    /** One line explaining why the controls are inert, or null when they are not. */
    fun blockedReason(locked: Boolean, armed: Boolean, importance: String): String? = when {
        locked && sensitive(importance) ->
            "Unlock this phone to read the question and answer it."
        locked -> "Unlock this phone to answer. Dismissing sends it to another device."
        !armed -> "Reading…"
        else -> null
    }

    /** Seconds left, for the countdown caption. Never negative. */
    fun secondsLeft(millisRemaining: Long): Long =
        if (millisRemaining <= 0L) 0L else millisRemaining / 1000L

    /**
     * What an activity that is going away without an explicit choice reports.
     *
     * Swiped away, killed, config-changed out of existence: all of it is
     * `dismissed`, never `answered`. The server escalates, which is the correct
     * reading of "the user did not deal with it here".
     */
    const val IMPLICIT_STATUS = CompanionProtocol.STATUS_DISMISSED
}
