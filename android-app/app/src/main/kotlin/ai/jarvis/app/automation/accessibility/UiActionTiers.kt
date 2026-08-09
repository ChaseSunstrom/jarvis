package ai.jarvis.app.automation.accessibility

import ai.jarvis.app.automation.actions.UiAutomationDelegate
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.automation.policy.Decision

/**
 * PURE LOGIC — no Android imports.
 *
 * The accessibility module's own local tier table, and the rule that decides
 * when it needs to ask on top of what the dispatcher already did.
 *
 * There are two local tables in this app, and that is deliberate rather than an
 * accident:
 *
 *  * `builtin/UiDelegatedActions.kt` — the dispatcher's table. It is the one the
 *    manifest advertises and the one `ActionRegistry` enforces.
 *  * this one — the table the code that actually moves a finger enforces.
 *
 * They agree on `ui_click` and `ui_type` (Tier 3 in both). They disagree on
 * navigation: the dispatcher rates `ui_scroll` / `ui_back` / `ui_home` /
 * `ui_open_recents` as Tier 2, on the reasonable grounds that scrolling commits
 * nothing. This module rates every operation that moves a finger as Tier 3,
 * because "commits nothing" is a property of the gesture, not of the screen
 * under it — Back can discard a draft, Home can drop a call, and a scroll on a
 * confirmation sheet followed by a tap is how the tap lands somewhere else than
 * the user was shown.
 *
 * Two tables that disagree could be a bug. Here it is a ratchet, and the ratchet
 * only turns one way: [needsLocalConfirmation] asks for a fresh human approval
 * exactly when the dispatcher would have run something without one. When the
 * dispatcher is already asking — which is the default for every one of these,
 * since a fresh install has no remembered answers — this module adds nothing and
 * the user sees one prompt, not two.
 *
 * The gap it closes is real and small: a user who sets "always allow" on
 * `ui_scroll` in the settings screen would otherwise have handed the server an
 * un-prompted gesture primitive.
 */
object UiActionTiers {

    /** Not in the dispatcher's table; implemented here for gesture fallbacks. */
    const val UI_SWIPE = "ui_swipe"

    /** Not in the dispatcher's table; the raw `performGlobalAction` escape hatch. */
    const val UI_GLOBAL_ACTION = "ui_global_action"

    /**
     * Everything that moves a finger or presses a system key. All Tier 3 here.
     */
    val ACTING: Set<String> = setOf(
        UiAutomationDelegate.UI_CLICK,
        UiAutomationDelegate.UI_TYPE,
        UiAutomationDelegate.UI_SCROLL,
        UiAutomationDelegate.UI_BACK,
        UiAutomationDelegate.UI_HOME,
        UiAutomationDelegate.UI_OPEN_RECENTS,
        UI_SWIPE,
        UI_GLOBAL_ACTION
    )

    /**
     * Read-only, but reading the screen is reading the user's bank balance, their
     * messages and their 2FA codes. Tier 2, never Tier 1.
     */
    val READING: Set<String> = setOf(
        UiAutomationDelegate.UI_READ_SCREEN,
        UiAutomationDelegate.UI_WAIT_FOR,
        UiAutomationDelegate.TAKE_SCREENSHOT
    )

    val ALL: Set<String> = ACTING + READING

    /**
     * The tier this module enforces. An id it has never heard of is [ActionTier.CONFIRM]:
     * unknown means dangerous, always.
     */
    fun tierFor(actionId: String): ActionTier = when (actionId) {
        in ACTING -> ActionTier.CONFIRM
        in READING -> ActionTier.NOTIFY
        else -> ActionTier.CONFIRM
    }

    fun isActing(actionId: String): Boolean = actionId in ACTING

    /**
     * Does this module have to raise its own consent prompt?
     *
     * @param actionId the delegated action about to run.
     * @param dispatcherDecision what `PolicyEngine` said for the dispatcher's
     *   (possibly lower) tier, recomputed here from the same store. Null when the
     *   dispatcher has no entry for this id at all — then nobody has asked.
     *
     * True exactly when this module says CONFIRM and the dispatcher did not put a
     * human in the loop. [Decision.ASK] means a human already saw the verbatim
     * parameters and tapped APPROVE for this very invocation, so asking again
     * would be noise, and noise is what trains people to tap APPROVE without
     * reading.
     */
    fun needsLocalConfirmation(actionId: String, dispatcherDecision: Decision?): Boolean =
        tierFor(actionId) == ActionTier.CONFIRM && dispatcherDecision != Decision.ASK

    /** Human-readable, for the audit note. */
    fun explain(actionId: String, dispatcherDecision: Decision?): String =
        "$actionId ui_local=${tierFor(actionId)} dispatcher=${dispatcherDecision ?: "none"} " +
            "-> ${if (needsLocalConfirmation(actionId, dispatcherDecision)) "ASK here" else "already gated"}"
}
