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
 *  * **Fail closed.** Every failure path returns `false`: the activity cannot
 *    start, notifications are blocked, the process is killed, the deferred is
 *    dropped, the countdown expires. There is no path that returns `true`
 *    without a human tapping APPROVE on [ApprovalActivity].
 *  * **No memory.** Nothing here writes to the policy store. A Tier-3 answer
 *    applies to exactly one request; the next one asks again. `allow_always`
 *    is meaningless at Tier 3 and must be treated as `ask` by callers.
 *  * **Verbatim parameters.** [params] is shown to the user as given. Pass the
 *    exact payload you are about to execute — not a summary, not the model's
 *    paraphrase. If what you execute can differ from what you passed here, the
 *    prompt is a lie and the gate is worthless.
 *  * **Untrusted input stops here.** Text from a web page, a notification or
 *    the screen may end up inside [params] or [reason] as *data*. It is
 *    rendered as text, never parsed for instructions, and it can never cause
 *    this function to return `true` on its own.
 *  * **Suspending, cancellable.** Cancelling the calling coroutine abandons the
 *    request (the prompt's own countdown then denies it). Cancellation
 *    propagates rather than being swallowed as a `false`.
 *
 * Callers must be prepared to wait up to [TIMEOUT_MS] plus a small delivery
 * grace before this returns.
 */
object ApprovalBridge {

    const val EXTRA_REQUEST_ID = "ai.jarvis.app.approval.REQUEST_ID"
    const val EXTRA_ACTION_ID = "ai.jarvis.app.approval.ACTION_ID"
    const val EXTRA_PARAMS = "ai.jarvis.app.approval.PARAMS"
    const val EXTRA_REASON = "ai.jarvis.app.approval.REASON"

    /** The prompt auto-denies after this long. Mirrored in [ApprovalActivity]. */
    const val TIMEOUT_MS = 60_000L

    /** Slack so the activity's own countdown always fires first. */
    private const val DELIVERY_GRACE_MS = 5_000L

    private const val TAG = "JarvisApproval"

    private val pending = ConcurrentHashMap<String, CompletableDeferred<Boolean>>()
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
    ): Boolean {
        val app = context.applicationContext
        val id = UUID.randomUUID().toString()
        val answer = CompletableDeferred<Boolean>()
        pending[id] = answer
        return try {
            if (!raisePrompt(app, id, actionId, params, reason)) {
                // We could not put the question in front of a human at all.
                Log.w(TAG, "no way to show the prompt for $actionId; denying")
                false
            } else {
                withTimeoutOrNull(TIMEOUT_MS + DELIVERY_GRACE_MS) { answer.await() } ?: false
            }
        } catch (ce: CancellationException) {
            // The caller went away. Propagate — do not report a decision that
            // nobody made.
            throw ce
        } catch (t: Throwable) {
            Log.e(TAG, "approval request for $actionId failed; denying", t)
            false
        } finally {
            pending.remove(id)
            clearNotification(app, id)
        }
    }

    /**
     * Called by [ApprovalActivity] with the human's answer. Unknown or
     * already-settled ids are ignored, so a stale activity cannot approve a
     * request that has since timed out.
     */
    fun deliver(requestId: String, approved: Boolean) {
        val deferred = pending.remove(requestId)
        if (deferred == null) {
            Log.w(TAG, "answer for unknown/expired request $requestId ignored")
            return
        }
        deferred.complete(approved)
    }

    /** True while a prompt for this id is still waiting for an answer. */
    fun isPending(requestId: String): Boolean = pending.containsKey(requestId)

    // --- raising the prompt -------------------------------------------------

    private fun raisePrompt(
        app: Context,
        id: String,
        actionId: String,
        params: String,
        reason: String,
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

        // Notification first. If the direct start is refused by background
        // activity-start restrictions, this is the user's only route to the
        // prompt, and posting it after a failed start would be too late.
        var reachable = postNotification(app, id, actionId, reason, intent)

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
    ): Boolean {
        val nm = app.getSystemService(NotificationManager::class.java) ?: return false
        val code = codes.incrementAndGet()
        val pi = PendingIntent.getActivity(
            app,
            code,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val notification = Notification.Builder(app, JarvisApp.CHANNEL_APPROVAL)
            .setSmallIcon(R.drawable.ic_jarvis_status)
            .setContentTitle("Approve “$actionId”?")
            .setContentText(reason)
            .setStyle(Notification.BigTextStyle().bigText(reason))
            .setCategory(Notification.CATEGORY_STATUS)
            .setContentIntent(pi)
            // On Android 14+ full-screen intents are reserved for calling and
            // alarm apps, so this usually degrades to a heads-up notification.
            // That is fine: it still puts the question in front of the user.
            .setFullScreenIntent(pi, true)
            .setOngoing(true)
            .setAutoCancel(false)
            // Never leak parameters onto a locked screen; the prompt itself
            // shows them once the user is past the keyguard.
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setTimeoutAfter(TIMEOUT_MS)
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
}
