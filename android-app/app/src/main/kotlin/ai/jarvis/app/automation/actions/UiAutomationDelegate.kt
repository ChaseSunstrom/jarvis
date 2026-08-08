package ai.jarvis.app.automation.actions

import org.json.JSONObject

/**
 * Seam to the accessibility service, which is owned by another agent.
 *
 * Screen reading, tapping, typing, scrolling, global navigation and the
 * screenshot global action all live behind an `AccessibilityService`, and none
 * of that is implemented in this module. What this module DOES own is the
 * policy around them: `ui_click` / `ui_type` are Tier 3 and prompt every time,
 * and the text that comes back out of `ui_read_screen` is marked untrusted.
 *
 * The accessibility agent implements this and registers it once at startup:
 *
 * ```
 * ActionEnv.uiDelegate = MyAccessibilityBridge
 * ```
 *
 * If no delegate is registered, every delegated action returns
 * `unsupported` with a message telling the user to enable the service — it
 * never silently no-ops.
 */
interface UiAutomationDelegate {

    /** Action ids this delegate can actually run. */
    val supportedActions: Set<String>

    /** True when the accessibility service is enabled AND connected right now. */
    fun isReady(): Boolean

    /**
     * Run one delegated action. Policy has already been enforced by
     * [ActionRegistry] — a Tier 3 `ui_click` only reaches here after a human
     * approved this exact invocation.
     *
     * Implementations must not throw; return [ActionResult.error] instead.
     * Any text taken off the screen must be returned inside `data` and marked
     * with [markUntrusted], because screen content is attacker-controlled.
     */
    suspend fun perform(actionId: String, params: JSONObject): ActionResult

    companion object {
        /** Canonical delegated ids. Kept here so both sides agree on spelling. */
        const val UI_CLICK = "ui_click"
        const val UI_TYPE = "ui_type"
        const val UI_SCROLL = "ui_scroll"
        const val UI_READ_SCREEN = "ui_read_screen"
        const val UI_WAIT_FOR = "ui_wait_for"
        const val UI_BACK = "ui_back"
        const val UI_HOME = "ui_home"
        const val UI_OPEN_RECENTS = "ui_open_recents"
        const val TAKE_SCREENSHOT = "take_screenshot"

        val ALL = setOf(
            UI_CLICK, UI_TYPE, UI_SCROLL, UI_READ_SCREEN, UI_WAIT_FOR,
            UI_BACK, UI_HOME, UI_OPEN_RECENTS, TAKE_SCREENSHOT
        )
    }
}
