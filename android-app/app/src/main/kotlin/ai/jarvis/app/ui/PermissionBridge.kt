package ai.jarvis.app.ui

import ai.jarvis.app.JarvisApp
import ai.jarvis.app.PermissionRequestActivity
import ai.jarvis.app.R
import ai.jarvis.app.compat.RuntimePermissions
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
 * Asking the OS for a dangerous permission, from code that has no Activity.
 *
 * ## Why this exists
 *
 * `requestPermissions` is a method on `Activity`. Every dangerous permission
 * Jarvis needs is needed by a command that arrives on a WebSocket, inside a
 * foreground service, with no Activity anywhere. So nothing ever asked, and
 * `send_sms`, `read_calendar`, `get_location` and the rest answered
 * `permission … not granted` forever — see [RuntimePermissions] for the whole
 * shape of that bug.
 *
 * This is the same trick [ApprovalBridge] plays for the Tier-3 consent screen:
 * a suspending call that raises a one-frame Activity, waits for its answer, and
 * fails closed on every path where the answer never arrives.
 *
 * ## Contract
 *
 *  * **Fail closed.** Every failure — no Activity could start, the process was
 *    killed, the dialog was dismissed, the countdown expired — reports the
 *    permission as still missing. Nothing here can make a caller believe it has
 *    a grant it does not have; the caller's own `checkSelfPermission` is still
 *    the authority, and the actions re-check anyway.
 *  * **It never asks for something the platform would drop.** The request is
 *    filtered through [RuntimePermissions.missing], so special access,
 *    background location and normal permissions never reach a dialog they
 *    cannot be granted from.
 *  * **It stops asking once the user means it.** Android answers a
 *    permanently-denied permission instantly, with no dialog. Re-requesting on
 *    every command would turn one refusal into an invisible Activity flash per
 *    command, so a permanent refusal is remembered [for the life of the
 *    process][permanentlyDenied] and short-circuits. It is deliberately not
 *    persisted: the user changes their mind in Settings, and a stale "no" on
 *    disk would outlive the grant.
 *  * **Suspending, cancellable.** Cancelling the caller abandons the request.
 */
object PermissionBridge {

    const val EXTRA_REQUEST_ID = "ai.jarvis.app.permission.REQUEST_ID"
    const val EXTRA_PERMISSIONS = "ai.jarvis.app.permission.PERMISSIONS"
    const val EXTRA_ACTION_ID = "ai.jarvis.app.permission.ACTION_ID"

    /**
     * How long to wait for the user to answer. Matches [ApprovalBridge]: a
     * permission dialog is quicker to read than a consent screen, but it can
     * sit behind the keyguard until the phone is unlocked.
     */
    const val TIMEOUT_MS = 60_000L

    private const val DELIVERY_GRACE_MS = 5_000L

    private const val TAG = "JarvisPermission"

    private val pending = ConcurrentHashMap<String, CompletableDeferred<List<String>>>()
    private val notificationIds = ConcurrentHashMap<String, Int>()
    private val codes = AtomicInteger(2000)

    /**
     * Permissions this user has refused with "don't ask again", for the life of
     * this process. See the contract above for why it is not persisted.
     */
    private val permanentlyDenied = ConcurrentHashMap.newKeySet<String>()

    /**
     * Ask for [permissions], and answer with the ones still missing afterwards.
     *
     * An empty result means every permission asked for is now held. A non-empty
     * one is the honest list to put in an error the model can act on.
     *
     * @param actionId what wanted them, shown in the notification fallback.
     */
    suspend fun ensure(
        context: Context,
        actionId: String,
        permissions: List<String>,
    ): List<String> {
        val app = context.applicationContext
        val wanted = RuntimePermissions.missing(app, permissions)
        if (wanted.isEmpty()) return emptyList()

        // A grant that arrived some other way — Settings, the checklist —
        // clears the memo, so one refusal is not a life sentence.
        permanentlyDenied.removeAll(permissions.filter { RuntimePermissions.isHeld(app, it) }.toSet())

        val askable = wanted.filterNot { permanentlyDenied.contains(it) }
        if (askable.isEmpty()) {
            Log.i(TAG, "not re-asking for $wanted; refused with don't-ask-again this session")
            return wanted
        }

        val id = UUID.randomUUID().toString()
        val answer = CompletableDeferred<List<String>>()
        pending[id] = answer
        return try {
            if (!raise(app, id, actionId, askable)) {
                Log.w(TAG, "no way to put a permission dialog on screen for $actionId")
                wanted
            } else {
                val still = withTimeoutOrNull(TIMEOUT_MS + DELIVERY_GRACE_MS) { answer.await() }
                // A timeout is not consent and not a refusal. Report what the
                // device actually holds rather than guessing either way.
                still ?: RuntimePermissions.missing(app, permissions)
            }
        } catch (ce: CancellationException) {
            throw ce
        } catch (t: Throwable) {
            Log.e(TAG, "permission request for $actionId failed", t)
            wanted
        } finally {
            pending.remove(id)
            clearNotification(app, id)
        }
    }

    /**
     * Called by [PermissionRequestActivity] with the outcome.
     *
     * @param stillMissing what the user did not grant.
     * @param permanent the subset the platform will no longer show a dialog
     *   for. Remembered so the next command does not flash an invisible
     *   Activity to be refused instantly again.
     */
    fun deliver(requestId: String, stillMissing: List<String>, permanent: List<String>) {
        permanentlyDenied.addAll(permanent)
        settle(requestId, stillMissing)
    }

    /**
     * The Activity went away without an answer — rotated out, killed, or backed
     * out of. Fail closed: whatever was asked for is still missing.
     */
    fun abandon(requestId: String, wanted: List<String>) {
        settle(requestId, wanted)
    }

    /** True while a request for this id is still waiting. */
    fun isPending(requestId: String): Boolean = pending.containsKey(requestId)

    /** Test seam: forget every remembered refusal. */
    fun forgetRefusals() = permanentlyDenied.clear()

    private fun settle(requestId: String, stillMissing: List<String>) {
        val deferred = pending.remove(requestId) ?: return
        deferred.complete(stillMissing)
    }

    // --- putting the dialog on screen ---------------------------------------

    private fun raise(
        app: Context,
        id: String,
        actionId: String,
        permissions: List<String>,
    ): Boolean {
        val intent = Intent(app, PermissionRequestActivity::class.java)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS or
                    Intent.FLAG_ACTIVITY_NO_USER_ACTION
            )
            .putExtra(EXTRA_REQUEST_ID, id)
            .putExtra(EXTRA_ACTION_ID, actionId)
            .putExtra(EXTRA_PERMISSIONS, permissions.toTypedArray())

        // Notification first, for the same reason ApprovalBridge does it: if
        // the direct start is refused by background activity-start rules, this
        // is the user's only route, and posting it afterwards would be too late.
        var reachable = postNotification(app, id, actionId, permissions, intent)
        try {
            app.startActivity(intent)
            reachable = true
        } catch (t: Throwable) {
            Log.w(TAG, "direct start of the permission prompt refused", t)
        }
        return reachable
    }

    private fun postNotification(
        app: Context,
        id: String,
        actionId: String,
        permissions: List<String>,
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
        val why = permissions
            .mapNotNull { RuntimePermissions.entryOf(it)?.why }
            .distinct()
            .joinToString(" ")
            .ifEmpty { "Jarvis needs a permission it has never been granted." }
        val notification = Notification.Builder(app, JarvisApp.CHANNEL_APPROVAL)
            .setSmallIcon(R.drawable.ic_jarvis_status)
            .setContentTitle("“$actionId” needs a permission")
            .setContentText(why)
            .setStyle(Notification.BigTextStyle().bigText(why))
            .setCategory(Notification.CATEGORY_STATUS)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .setTimeoutAfter(TIMEOUT_MS)
            .build()
        return try {
            nm.notify(code, notification)
            notificationIds[id] = code
            true
        } catch (t: Throwable) {
            Log.w(TAG, "could not post the permission notification", t)
            false
        }
    }

    private fun clearNotification(app: Context, id: String) {
        val code = notificationIds.remove(id) ?: return
        try {
            app.getSystemService(NotificationManager::class.java)?.cancel(code)
        } catch (t: Throwable) {
            Log.w(TAG, "could not cancel the permission notification", t)
        }
    }
}
