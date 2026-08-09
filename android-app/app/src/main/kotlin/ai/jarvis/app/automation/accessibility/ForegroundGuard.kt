package ai.jarvis.app.automation.accessibility

/**
 * PURE LOGIC — no Android imports.
 *
 * Which app are we actually about to drive, and is it the one the human was
 * looking at when they approved?
 *
 * This exists because of a specific, boring, fatal sequencing problem. Jarvis'
 * consent prompt is a Jarvis activity. Raising it **backgrounds the app the
 * command is aimed at**, and `ApprovalActivity` delivers the answer to the
 * waiting coroutine *before* it calls `finish()`. So at the instant the
 * dispatcher resumes and calls `execute()`, the foreground app is still
 * `ai.jarvis.app`. Whatever reads the foreground at that moment sees Jarvis,
 * not the target — and a moment later sees whichever app the system happens to
 * bring forward.
 *
 * Two things follow, and both matter:
 *
 *  * Reading the foreground *once*, immediately after an approval, answers a
 *    question nobody asked. It has to settle first.
 *  * "Settled" cannot mean "whatever turns up". It has to mean the app that was
 *    in front before Jarvis covered it — the app the human was looking at. If
 *    something else comes forward instead, that is a click-jacking primitive
 *    and the only safe answer is to refuse.
 *
 * The rules are here, in a file with no Android in it, so
 * `android-app/tools/screen_prune_test.py` can run them.
 */
object ForegroundGuard {

    /** How long to wait for the approved app to come back to the front. */
    const val SETTLE_TIMEOUT_MS = 3_000L

    /** Poll interval while waiting. */
    const val POLL_MS = 100L

    /**
     * How recently a Jarvis consent surface must have been in front for this
     * module to believe the dispatcher's claim that a human was asked.
     *
     * The dispatcher holds a 15 s timeout over `execute()`, so on a real
     * approval this age is a few milliseconds and never more than a couple of
     * seconds. The window is generous because the cost of it being too tight is
     * a refused gesture on a slow device, and the cost of it being absent
     * altogether is a gesture nobody approved.
     */
    const val CONSENT_EVIDENCE_MS = 30_000L

    /** Jarvis' own package, in any of its process/activity flavours. */
    fun isSelf(packageName: String?): Boolean {
        val pkg = PackageDenylist.normalize(packageName)
        return pkg == PackageDenylist.SELF_PACKAGE ||
            pkg.startsWith(PackageDenylist.SELF_PACKAGE + ".")
    }

    fun isUnknown(packageName: String?): Boolean =
        PackageDenylist.normalize(packageName).isEmpty()

    /** What to do with the foreground as it looks right now. */
    sealed class Plan {
        /** A real third-party app is in front; act on this one. */
        data class Ready(val packageName: String) : Plan()

        /**
         * Jarvis' own UI is in front — almost always our consent prompt on its
         * way out. Wait for [expected] (the last app that was really in front)
         * to come back, and refuse if it does not.
         */
        data class AwaitReturn(val expected: String) : Plan()

        /** Nothing usable to act on. Nothing runs. */
        data class Refuse(val reason: String) : Plan()
    }

    /**
     * @param current the foreground package as read this instant.
     * @param lastForeign the most recent foreground package that was NOT ours.
     */
    fun plan(current: String?, lastForeign: String?): Plan = when {
        isUnknown(current) -> Plan.Refuse(
            "the foreground app could not be identified, so there is nothing safe to act on"
        )

        !isSelf(current) -> Plan.Ready(PackageDenylist.normalize(current))

        isUnknown(lastForeign) || isSelf(lastForeign) -> Plan.Refuse(
            "Jarvis' own screen is in front and no other app has been seen since the " +
                "accessibility service started; there is nothing to act on but ourselves, " +
                "and Jarvis never drives its own UI"
        )

        else -> Plan.AwaitReturn(PackageDenylist.normalize(lastForeign))
    }

    /**
     * Is [now] still the app that was approved?
     *
     * Blank on either side is false: "we do not know" is not "yes". Used both
     * after the settle and on every iteration of a long poll like
     * `ui_wait_for`, which would otherwise keep reading whatever app the user
     * switched to during its sixty seconds.
     */
    fun sameTarget(approved: String?, now: String?): Boolean {
        val a = PackageDenylist.normalize(approved)
        val b = PackageDenylist.normalize(now)
        return a.isNotEmpty() && a == b
    }

    /**
     * May this module believe the dispatcher already put a human in the loop?
     *
     * [ageMs] is how long ago a Jarvis consent surface was last in front.
     * A real Tier-3 approval leaves that evidence a few milliseconds old,
     * because the prompt is a Jarvis activity that was covering the screen
     * moments earlier. A caller that reached the delegate *without* going
     * through a prompt leaves no such trace, and this is what turns
     * "`ActionRegistry` says it asked" from an assumption into a check.
     */
    fun hasConsentEvidence(ageMs: Long): Boolean =
        ageMs >= 0L && ageMs <= CONSENT_EVIDENCE_MS

    /** The refusal wording, kept next to the rule it explains. */
    fun noConsentEvidenceMessage(actionId: String): String =
        "$actionId reached the accessibility layer as though a human had just confirmed " +
            "it, but no Jarvis consent screen has been in front of the user in the last " +
            "${CONSENT_EVIDENCE_MS / 1000} seconds. Every gesture is performed within " +
            "moments of somebody being asked, so this did not come from an approval. " +
            "Nothing was done."

    /** The refusal wording when the approved app never came back. */
    fun lostTargetMessage(expected: String, nowSelf: Boolean): String =
        "the approved app ($expected) did not come back to the foreground within " +
            "${SETTLE_TIMEOUT_MS / 1000} seconds after the confirmation prompt" +
            (if (nowSelf) " — Jarvis' own UI is still in front" else "") +
            "; nothing was done"
}
