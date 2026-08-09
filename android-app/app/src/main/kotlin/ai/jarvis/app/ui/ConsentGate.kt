package ai.jarvis.app.ui

/**
 * PURE LOGIC — no Android imports, no I/O, no clock. Unit-testable on a plain
 * JVM (`ConsentGateTest`).
 *
 * When the Tier-3 consent screen is allowed to show its parameters and when
 * APPROVE is allowed to do anything. Three inputs, and the rules are the file:
 *
 *  1. **Locked means nothing.** A consent prompt renders over the keyguard so
 *     the phone lights up and the question is seen — but a locked phone is a
 *     phone in someone else's hand. While the keyguard is up the parameters are
 *     hidden (they name people, numbers, message bodies, shell commands) and
 *     APPROVE is inert. "A human tapped approve" has to mean *the* human.
 *  2. **Unarmed means nothing.** APPROVE stays inert for a moment after the
 *     prompt becomes readable, so a tap already in flight — or an overlay
 *     timing one — cannot land on a button that only just appeared.
 *  3. **Answered means nothing.** One prompt answers exactly once.
 *
 * DENY is deliberately live in every one of those states: refusing is safe from
 * a locked screen, from a stranger, and from a stray tap. The asymmetry is the
 * point — every state that is uncertain must be able to say no and must not be
 * able to say yes.
 */
object ConsentGate {

    /** How long APPROVE stays inert after the prompt becomes readable. */
    const val ARM_MS = 700L

    /** Shown in place of the parameters while the keyguard is up. */
    const val LOCKED_PARAMS = "Hidden until this phone is unlocked."

    /** Shown when there is nothing to show. */
    const val NO_PARAMS = "(no parameters)"

    /** Verbatim parameters are never rendered over the keyguard. */
    fun paramsVisible(locked: Boolean): Boolean = !locked

    /**
     * APPROVE is live only when the device is unlocked, the prompt has been
     * readable for [ARM_MS], and nothing has answered yet. Any doubt is a no.
     */
    fun approveEnabled(locked: Boolean, armed: Boolean, answered: Boolean): Boolean =
        !locked && armed && !answered

    /** DENY is live whenever the prompt is unanswered, locked or not. */
    fun denyEnabled(answered: Boolean): Boolean = !answered

    /**
     * What the parameter block should read, given the lock state and the
     * verbatim text. Never returns the parameters while locked.
     */
    fun paramsText(locked: Boolean, params: String): String = when {
        locked -> LOCKED_PARAMS
        params.isEmpty() -> NO_PARAMS
        else -> params
    }

    /** One line under the buttons explaining why APPROVE is inert, if it is. */
    fun blockedReason(locked: Boolean, armed: Boolean): String? = when {
        locked -> "Unlock this phone to see what it wants to do and to approve it."
        !armed -> "Reading…"
        else -> null
    }
}
