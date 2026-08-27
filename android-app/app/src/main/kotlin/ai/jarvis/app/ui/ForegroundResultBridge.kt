package ai.jarvis.app.ui

import ai.jarvis.app.automation.actions.ActionResult
import android.content.Context
import android.content.Intent
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeoutOrNull
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * An action that can only be finished by an Activity in the foreground, run
 * from code that has none.
 *
 * ## Why this exists
 *
 * Two of the Tasker rows (M61) end in an Activity result rather than a system
 * service call: a barcode is decoded by whichever scanner app answers the
 * `com.google.zxing.client.android.SCAN` intent and comes back through
 * `onActivityResult`, and an NFC tag is only ever handed to an Activity that is
 * resumed with reader mode on. Every command arrives in a Service. This is the
 * [PermissionBridge] trick a third time: raise a one-frame Activity, suspend
 * until it reports, fail closed on every path where it never does.
 *
 * ## What it deliberately does NOT do
 *
 * No notification fallback, unlike [PermissionBridge]. A permission is an
 * offer that stays true for hours, so leaving a notification for the user to
 * tap later is the right thing. "Scan this code" and "hold a tag to the phone"
 * are not: the person asked *now*, and a stale notification tapped an hour
 * later would open a camera nobody is pointing at anything. So when the
 * platform drops the start — Android refuses background activity starts, and
 * refuses them silently — the action reports that in one sentence and stops.
 *
 * ## Contract
 *
 *  * **Fail closed.** No answer is an error result, never a success. The
 *    Activity settling twice is harmless; the second answer is dropped.
 *  * **The start is verified.** `startActivity` returning proves nothing (a
 *    refused background start logs and drops the intent), so the caller waits
 *    [START_GRACE_MS] for the Activity to say it is on screen.
 *  * **Suspending, cancellable.** The dispatcher's own `withTimeout` can cancel
 *    this; the Activity then finishes itself on its own clock.
 */
object ForegroundResultBridge {

    const val EXTRA_REQUEST_ID = "ai.jarvis.app.result.REQUEST_ID"

    /**
     * How long an accepted start gets to reach the screen. Same figure and
     * same reasoning as [PermissionBridge]: a start that is going to happen has
     * happened well inside this, and one the platform dropped never will.
     */
    private const val START_GRACE_MS = 4_000L

    /** Slack over the Activity's own timeout, so its answer beats ours. */
    private const val DELIVERY_GRACE_MS = 5_000L

    private const val TAG = "JarvisResult"

    private val pending = ConcurrentHashMap<String, CompletableDeferred<ActionResult>>()

    /** Completed by [raised] the moment the host Activity actually runs. */
    private val onScreen = ConcurrentHashMap<String, CompletableDeferred<Unit>>()

    /**
     * Start [intent] (an Activity of this app that calls [raised] then
     * [deliver]) and wait for what it reports.
     *
     * @param what a few words for the error messages: "the barcode scanner".
     * @param timeoutMs how long the Activity may take to answer; the Activity
     *   is expected to give up on its own before this.
     */
    suspend fun run(context: Context, intent: Intent, what: String, timeoutMs: Long): ActionResult {
        val app = context.applicationContext
        val id = UUID.randomUUID().toString()
        val answer = CompletableDeferred<ActionResult>()
        pending[id] = answer
        onScreen[id] = CompletableDeferred()
        intent.putExtra(EXTRA_REQUEST_ID, id)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS or
                    Intent.FLAG_ACTIVITY_NO_USER_ACTION
            )
        // The assist surface finishes itself in onStop unless one of our own
        // prompts is up (see JarvisAssistActivity); a scanner or a tag prompt
        // over it is ours, and the conversation has to survive it.
        PromptPresence.raised()
        try {
            try {
                app.startActivity(intent)
            } catch (t: Throwable) {
                Log.w(TAG, "could not start $what", t)
                return ActionResult.error("could not open $what: ${t.message ?: t.javaClass.simpleName}")
            }
            if (withTimeoutOrNull(START_GRACE_MS) { onScreen[id]?.await() } == null) {
                Log.w(TAG, "$what never reached the screen")
                return ActionResult.error(
                    "$what never reached the screen: Android refuses to open it while Jarvis is in " +
                        "the background. Open Jarvis, or ask from the phone, and try again"
                )
            }
            return withTimeoutOrNull(timeoutMs + DELIVERY_GRACE_MS) { answer.await() }
                ?: ActionResult.error("$what gave no answer within ${timeoutMs / 1000} seconds")
        } finally {
            PromptPresence.settled()
            pending.remove(id)
            onScreen.remove(id)
        }
    }

    /** Called by the host Activity as it starts: the only proof the start was not dropped. */
    fun raised(requestId: String) {
        onScreen[requestId]?.complete(Unit)
    }

    /** Called by the host Activity with the outcome. A second call for the same id is ignored. */
    fun deliver(requestId: String, result: ActionResult) {
        pending.remove(requestId)?.complete(result)
    }

    /**
     * The Activity went away without answering — backed out of, killed, or
     * finished by its own clock. Fail closed; a no-op once [deliver] has run.
     */
    fun abandon(requestId: String, what: String) {
        deliver(requestId, ActionResult.error("$what was closed before it answered"))
    }

    /** True while a request for this id is still waiting; the Activity stops work once it is not. */
    fun isPending(requestId: String): Boolean = pending.containsKey(requestId)
}
