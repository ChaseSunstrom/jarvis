package ai.jarvis.app.automation.actions.builtin

import android.content.Context
import ai.jarvis.app.automation.actions.ActionEnv
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.UiAutomationDelegate
import ai.jarvis.app.automation.policy.ActionTier
import org.json.JSONObject

/**
 * Screen reading and UI automation. NONE of this is implemented here — the
 * accessibility agent owns the service and registers a [UiAutomationDelegate]
 * on [ActionEnv]. What lives here is the half that must not be delegated: the
 * ids, and the tier each one carries.
 *
 * Tiering, straight from the shared brief:
 *
 *  * `ui_click`, `ui_type` — Tier 3. They tap and they type; that is how a
 *    form gets submitted, a payment gets confirmed and a message gets sent.
 *    Every invocation shows the real selector and the real text.
 *  * `ui_scroll`, `ui_back`, `ui_home`, `ui_open_recents` — Tier 2. Navigation
 *    that moves the view without committing anything.
 *  * `ui_read_screen`, `ui_wait_for` — Tier 2, not Tier 1: read-only, but it
 *    reads everything on screen, which is the user's bank balance as often as
 *    it is a button label.
 *  * `take_screenshot` — Tier 2, and only via the accessibility global action.
 *
 * Everything these return is attacker-controlled text. The delegate marks it
 * untrusted, and the dispatcher refuses to auto-allow any action derived from
 * it.
 */
internal class DelegatedUiAction(
    override val id: String,
    override val tier: ActionTier,
    override val description: String,
    override val paramsSchema: Map<String, String>,
    override val capability: String = "ui_automation",
    override val timeoutMs: Long = 15_000L,
    override val untrustedOutput: Boolean = false
) : JarvisAction {

    override val delegated = true

    override val unsupportedReason: String =
        "the Jarvis accessibility service is not running; enable it in " +
            "Settings > Accessibility > Jarvis and retry"

    override fun isAvailable(ctx: Context): Boolean {
        val delegate = ActionEnv.uiDelegate ?: return false
        return delegate.isReady() && id in delegate.supportedActions
    }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val delegate = ActionEnv.uiDelegate
            ?: return ActionResult.unsupported(unsupportedReason)
        if (id !in delegate.supportedActions) {
            return ActionResult.unsupported("$id is not implemented by the accessibility service")
        }
        return delegate.perform(id, params)
    }
}

object UiActions {

    val click: JarvisAction = DelegatedUiAction(
        id = UiAutomationDelegate.UI_CLICK,
        tier = ActionTier.CONFIRM,
        description = "Tap an on-screen element identified by text, content description or id.",
        paramsSchema = mapOf(
            "text" to "string: visible text to match",
            "content_description" to "string: accessibility label to match",
            "view_id" to "string: full resource id, e.g. com.app:id/send",
            "index" to "int (optional): which match, when several"
        )
    )

    val type: JarvisAction = DelegatedUiAction(
        id = UiAutomationDelegate.UI_TYPE,
        tier = ActionTier.CONFIRM,
        description = "Type text into the focused field, or into a field identified by a selector.",
        paramsSchema = mapOf(
            "text" to "string: the text to type",
            "view_id" to "string (optional): target field resource id",
            "clear" to "bool (optional): clear the field first"
        )
    )

    val scroll: JarvisAction = DelegatedUiAction(
        id = UiAutomationDelegate.UI_SCROLL,
        tier = ActionTier.NOTIFY,
        description = "Scroll the screen or a scrollable element.",
        paramsSchema = mapOf(
            "direction" to "string: up | down | left | right",
            "amount" to "int (optional): number of scroll steps, default 1"
        )
    )

    val readScreen: JarvisAction = DelegatedUiAction(
        id = UiAutomationDelegate.UI_READ_SCREEN,
        tier = ActionTier.NOTIFY,
        description = "Read the text and controls currently on screen (returns UNTRUSTED content).",
        paramsSchema = mapOf(
            "include_invisible" to "bool (optional): include off-screen nodes"
        ),
        // Everything this returns is another app's text.
        untrustedOutput = true
    )

    val waitFor: JarvisAction = DelegatedUiAction(
        id = UiAutomationDelegate.UI_WAIT_FOR,
        tier = ActionTier.NOTIFY,
        description = "Wait until an element appears on screen, or time out.",
        paramsSchema = mapOf(
            "text" to "string: text to wait for",
            "view_id" to "string (optional): resource id to wait for",
            "timeout_ms" to "int: how long to wait (default 10000)"
        ),
        timeoutMs = 65_000L,
        untrustedOutput = true
    )

    val back: JarvisAction = DelegatedUiAction(
        id = UiAutomationDelegate.UI_BACK,
        tier = ActionTier.NOTIFY,
        description = "Press the system Back button.",
        paramsSchema = emptyMap()
    )

    val home: JarvisAction = DelegatedUiAction(
        id = UiAutomationDelegate.UI_HOME,
        tier = ActionTier.NOTIFY,
        description = "Press the system Home button.",
        paramsSchema = emptyMap()
    )

    val recents: JarvisAction = DelegatedUiAction(
        id = UiAutomationDelegate.UI_OPEN_RECENTS,
        tier = ActionTier.NOTIFY,
        description = "Open the recent apps switcher.",
        paramsSchema = emptyMap()
    )

    /**
     * Tier 2. Implemented by the accessibility service's global screenshot
     * action (API 30+) — there is deliberately no MediaProjection path here,
     * because that would put a persistent screen-capture consent in front of
     * the user for something an automation triggers.
     */
    val screenshot: JarvisAction = DelegatedUiAction(
        id = UiAutomationDelegate.TAKE_SCREENSHOT,
        tier = ActionTier.NOTIFY,
        description = "Take a screenshot of the current screen.",
        paramsSchema = mapOf(
            "save" to "bool (optional): save into Jarvis storage instead of returning bytes"
        ),
        capability = "screenshot",
        timeoutMs = 20_000L,
        // A screenshot is a picture of somebody else's screen.
        untrustedOutput = true
    )

    val all: List<JarvisAction> = listOf(
        click, type, scroll, readScreen, waitFor, back, home, recents, screenshot
    )
}
