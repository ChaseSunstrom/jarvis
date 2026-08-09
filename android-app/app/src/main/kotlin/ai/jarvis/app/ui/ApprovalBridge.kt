package ai.jarvis.app.ui

import ai.jarvis.app.ApprovalActivity
import ai.jarvis.app.JarvisApp
import ai.jarvis.app.R
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeoutOrNull
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/**
 * The Tier-3 consent gate. This is the only supported way for any module to ask
 * a human "may I actually do this?", and the answer is a plain Boolean:
 *
 * ```
 * val ok = ApprovalBridge.request(
 *     context,
 *     actionId = "sms.send",
 *     params = """{"to":"+441234567890","body":"On my way"}""",
 *     reason = "You asked me to tell Sam you are running late.",
 * )
 * if (!ok) return DeviceResult.denied(commandId)
 * ```
 *
 * Contract — every one of these is load-bearing, do not soften any of them:
 *
 *  * **Fail closed.** Every failure path denies: the activity cannot start,
 *    notifications are blocked, the process is killed, the deferred is dropped,
 *    the countdown expires. There is no path that approves without a human
 *    tapping APPROVE on [ApprovalActivity] — and that button is inert until the
 *    keyguard is gone, so it means the phone's owner, not whoever picked it up.
 *  * **No memory.** Nothing here writes to the policy store, and no overload
 *    can ever answer "always". A Tier-3 answer applies to exactly one request;
 *    the next one asks again.
 *  * **Verbatim parameters.** [params] is shown to the user as given. Pass the
 *    exact payload you are about to execute — not a summary, not the model's
 *    paraphrase. If what you execute can differ from what you passed here, the
 *    prompt is a lie and the gate is worthless.
 *  * **Untrusted input stops here.** Text from a web page, a notification or
 *    the screen may end up inside [params] or `reason` as *data*. It is
 *    rendered as text, never parsed for instructions, and it can never cause
 *    this function to approve on its own.
 *  * **Suspending, cancellable.** Cancelling the calling coroutine abandons the
 *    request (the prompt's own countdown then denies it). Cancellation
 *    propagates rather than being swallowed as a denial.
 *
 * Callers must be prepared to wait up to [TIMEOUT_MS] plus a small delivery
 * grace before this returns.
 */
object ApprovalBridge {

    const val EXTRA_REQUEST_ID = "ai.jarvis.app.approval.REQUEST_ID"
    const val EXTRA_ACTION_ID = "ai.jarvis.app.approval.ACTION_ID"
    const val EXTRA_PARAMS = "ai.jarvis.app.approval.PARAMS"
    const val EXTRA_REASON = "ai.jarvis.app.approval.REASON"

    /** Optional: the action's own description from the device-local table. */
    const val EXTRA_DESCRIPTION = "ai.jarvis.app.approval.DESCRIPTION"

    /** Optional: display label for the tier, e.g. "TIER 3 · CONFIRM". */
    const val EXTRA_TIER_LABEL = "ai.jarvis.app.approval.TIER_LABEL"

    /** Optional: the server's command id, shown as provenance. */
    const val EXTRA_COMMAND_ID = "ai.jarvis.app.approval.COMMAND_ID"

    /** How long the prompt's countdown runs, already clamped by [clampTimeout]. */
    const val EXTRA_TIMEOUT_MS = "ai.jarvis.app.approval.TIMEOUT_MS"

    /** The prompt auto-denies after this long, and no caller may extend it. */
    const val TIMEOUT_MS = 60_000L

    /** Floor for a caller-supplied timeout; below this nobody could read it. */
    const val MIN_TIMEOUT_MS = 10_000L

    /** Slack so the activity's own countdown always fires first. */
    private const val DELIVERY_GRACE_MS = 5_000L

    private const val TAG = "JarvisApproval"

    /** What actually happened, so callers can log a denial apart from a lapse. */
    enum class Outcome {
        APPROVED,
        DENIED,
        TIMED_OUT,
        /** The prompt could not be put in front of a human at all. */
        UNDELIVERABLE;

        val approved: Boolean get() = this == APPROVED
    }

    private val pending = ConcurrentHashMap<String, CompletableDeferred<Outcome>>()
    private val notificationIds = ConcurrentHashMap<String, Int>()
    private val codes = AtomicInteger(1000)

    /**
     * Show the full-screen consent prompt and suspend until the human answers.
     *
     * @param actionId local action id, e.g. `sms.send` — shown verbatim.
     * @param params the exact parameters about to be executed, ideally JSON.
     * @param reason human-readable why, shown verbatim.
     * @return true only if the user tapped APPROVE. Anything else is false.
     */
    suspend fun request(
        context: Context,
        actionId: String,
        params: String,
        reason: String,
    ): Boolean = requestOutcome(context, actionId, params, reason).approved

    /**
     * As [request], but reports how the request ended. Same guarantees; the
     * extra detail exists so the audit log can say "timed out" instead of
     * flattening everything into "denied".
     *
     * @param description the action's own description from the device-local
     *   action table. Trusted, unlike [reason], and labelled as such on screen.
     * @param tierLabel display-only tier text; defaults to the Tier-3 wording.
     * @param commandId the server's command id, shown as provenance.
     * @param timeoutMs clamped to [MIN_TIMEOUT_MS]..[TIMEOUT_MS]. A caller may
     *   ask for a shorter prompt, never a longer one — how long a consent
     *   prompt lives is not something a remote server gets to decide.
     */
    suspend fun requestOutcome(
        context: Context,
        actionId: String,
        params: String,
        reason: String,
        description: String = "",
        tierLabel: String = DEFAULT_TIER_LABEL,
        commandId: String? = null,
        timeoutMs: Long = TIMEOUT_MS,
    ): Outcome {
        val app = context.applicationContext
        val id = UUID.randomUUID().toString()
        val effectiveTimeout = clampTimeout(timeoutMs)
        val answer = CompletableDeferred<Outcome>()
        pending[id] = answer
        return try {
            val shown = raisePrompt(
                app = app,
                id = id,
                actionId = actionId,
                params = params,
                reason = reason,
                description = description,
                tierLabel = tierLabel,
                commandId = commandId,
                timeoutMs = effectiveTimeout,
            )
            if (!shown) {
                Log.w(TAG, "no way to show the prompt for $actionId; denying")
                Outcome.UNDELIVERABLE
            } else {
                withTimeoutOrNull(effectiveTimeout + DELIVERY_GRACE_MS) { answer.await() }
                    ?: Outcome.TIMED_OUT
            }
        } catch (ce: CancellationException) {
            // The caller went away. Propagate — do not report a decision that
            // nobody made.
            throw ce
        } catch (t: Throwable) {
            Log.e(TAG, "approval request for $actionId failed; denying", t)
            Outcome.DENIED
        } finally {
            pending.remove(id)
            clearNotification(app, id)
        }
    }

    /**
     * Source-compatible entry point for the automation module's
     * `UiApprovalGateway`, which was written against a wider signature.
     *
     * Two things are deliberately not honoured:
     *
     *  * `rememberable` is ignored. This prompt has no "always allow" control
     *    and this function never returns `approved_always`, so a Tier-2 caller
     *    that wanted a remembered answer simply gets asked again. Erring toward
     *    more prompting is the only safe direction to err in.
     *  * `timeoutMs` is clamped to at most [TIMEOUT_MS].
     *
     * @param tier accepted as [Any] so this module never imports the automation
     *   module's types; only its string form is displayed.
     * @return `"approved"`, `"denied"` or `"timeout"`.
     */
    @Suppress("LongParameterList", "UNUSED_PARAMETER")
    suspend fun request(
        context: Context,
        actionId: String,
        description: String,
        params: Any?,
        tier: Any?,
        reason: String,
        commandId: String?,
        rememberable: Boolean,
        timeoutMs: Long,
    ): String {
        val outcome = requestOutcome(
            context = context,
            actionId = actionId,
            // toString() on a JSONObject is its exact serialisation, which is
            // what "verbatim" means for a structured payload.
            params = params?.toString().orEmpty(),
            reason = reason,
            description = description,
            tierLabel = tierLabelOf(tier),
            commandId = commandId,
            timeoutMs = timeoutMs,
        )
        return when (outcome) {
            Outcome.APPROVED -> "approved"
            Outcome.TIMED_OUT -> "timeout"
            Outcome.DENIED, Outcome.UNDELIVERABLE -> "denied"
        }
    }

    /**
     * Called by [ApprovalActivity] with the human's answer. Unknown or
     * already-settled ids are ignored, so a stale activity cannot approve a
     * request that has since timed out.
     */
    fun deliver(requestId: String, approved: Boolean) {
        settle(requestId, if (approved) Outcome.APPROVED else Outcome.DENIED)
    }

    /** Called by [ApprovalActivity] when its countdown runs out. */
    fun deliverTimeout(requestId: String) {
        settle(requestId, Outcome.TIMED_OUT)
    }

    /** True while a prompt for this id is still waiting for an answer. */
    fun isPending(requestId: String): Boolean = pending.containsKey(requestId)

    /** Callers may shorten the prompt but never lengthen it. */
    fun clampTimeout(requested: Long): Long = when {
        requested <= 0L -> TIMEOUT_MS
        else -> requested.coerceIn(MIN_TIMEOUT_MS, TIMEOUT_MS)
    }

    private fun settle(requestId: String, outcome: Outcome) {
        val deferred = pending.remove(requestId)
        if (deferred == null) {
            Log.w(TAG, "answer for unknown/expired request $requestId ignored")
            return
        }
        deferred.complete(outcome)
    }

    /** Display-only. Accepts the automation module's ActionTier by name. */
    private fun tierLabelOf(tier: Any?): String = when (tier?.toString()?.uppercase()) {
        "AUTO", "1", "TIER1" -> "TIER 1 · AUTO"
        "NOTIFY", "2", "TIER2" -> "TIER 2 · NOTIFY"
        else -> DEFAULT_TIER_LABEL
    }

    // --- raising the prompt -------------------------------------------------

    private fun raisePrompt(
        app: Context,
        id: String,
        actionId: String,
        params: String,
        reason: String,
        description: String,
        tierLabel: String,
        commandId: String?,
        timeoutMs: Long,
    ): Boolean {
        val intent = Intent(app, ApprovalActivity::class.java)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS or
                    Intent.FLAG_ACTIVITY_NO_USER_ACTION
            )
            .putExtra(EXTRA_REQUEST_ID, id)
            .putExtra(EXTRA_ACTION_ID, actionId)
            .putExtra(EXTRA_PARAMS, params)
            .putExtra(EXTRA_REASON, reason)
            .putExtra(EXTRA_DESCRIPTION, description)
            .putExtra(EXTRA_TIER_LABEL, tierLabel)
            .putExtra(EXTRA_COMMAND_ID, commandId)
            .putExtra(EXTRA_TIMEOUT_MS, timeoutMs)

        // Notification first. If the direct start is refused by background
        // activity-start restrictions, this is the user's only route to the
        // prompt, and posting it after a failed start would be too late.
        var reachable = postNotification(app, id, actionId, reason, intent, timeoutMs)

        try {
            app.startActivity(intent)
            reachable = true
        } catch (t: Throwable) {
            // Expected when the app is backgrounded without the
            // display-over-other-apps grant. The notification carries it.
            Log.w(TAG, "direct start of the consent prompt refused", t)
        }
        return reachable
    }

    private fun postNotification(
        app: Context,
        id: String,
        actionId: String,
        reason: String,
        intent: Intent,
        timeoutMs: Long,
    ): Boolean {
        val nm = app.getSystemService(NotificationManager::class.java) ?: return false
        val code = codes.incrementAndGet()
        val pi = PendingIntent.getActivity(
            app,
            code,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val summary = reason.ifEmpty { "No reason given." }
        val notification = Notification.Builder(app, JarvisApp.CHANNEL_APPROVAL)
            .setSmallIcon(R.drawable.ic_jarvis_status)
            .setContentTitle("Approve “$actionId”?")
            .setContentText(summary)
            .setStyle(Notification.BigTextStyle().bigText(summary))
            .setCategory(Notification.CATEGORY_STATUS)
            .setContentIntent(pi)
            // On Android 14+ full-screen intents are reserved for calling and
            // alarm apps, so this usually degrades to a heads-up notification.
            // That is fine: it still puts the question in front of the user.
            .setFullScreenIntent(pi, true)
            .setOngoing(true)
            .setAutoCancel(false)
            // Never leak parameters onto a locked screen. The prompt itself
            // enforces the other half of that: while the keyguard is up it
            // hides the parameters and keeps APPROVE inert, so this is not the
            // only thing standing between a locked phone and a sent SMS.
            // See ai.jarvis.app.ui.ConsentGate.
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setTimeoutAfter(timeoutMs)
            .build()
        return try {
            nm.notify(code, notification)
            notificationIds[id] = code
            true
        } catch (t: Throwable) {
            Log.w(TAG, "could not post the approval notification", t)
            false
        }
    }

    private fun clearNotification(app: Context, id: String) {
        val code = notificationIds.remove(id) ?: return
        try {
            app.getSystemService(NotificationManager::class.java)?.cancel(code)
        } catch (t: Throwable) {
            Log.w(TAG, "could not cancel the approval notification", t)
        }
    }

    private const val DEFAULT_TIER_LABEL = "TIER 3 · CONFIRMATION REQUIRED"
}
