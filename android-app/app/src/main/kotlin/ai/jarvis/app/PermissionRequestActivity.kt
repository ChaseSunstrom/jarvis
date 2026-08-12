package ai.jarvis.app

import ai.jarvis.app.compat.RuntimePermissions
import ai.jarvis.app.ui.PermissionBridge
import android.app.Activity
import android.app.KeyguardManager
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log

/**
 * One frame of Activity, so that asking for a dangerous permission is possible
 * at all.
 *
 * `requestPermissions` is a method on `Activity`, and everything in Jarvis that
 * needs a dangerous permission — a `device_command` off the WebSocket, a task
 * step, a trigger — runs in a Service. That mismatch is the whole reason the
 * permissions were declared and never requested. This is the [ListenTrampolineActivity]
 * pattern applied to the other side of it: appear, ask, report, disappear.
 *
 * Two manifest details are load-bearing and easy to get wrong:
 *
 *  * **No `android:noHistory`.** The permission dialog is a separate activity,
 *    so a `noHistory` host is finished the moment it loses the foreground and
 *    the result lands on a dead window. The listen trampoline sets it and is
 *    right to; this one must not.
 *  * **`configChanges` covers rotation.** A recreated host would be a second
 *    request racing the first, and [PermissionBridge.abandon] on the destroyed
 *    one would settle the answer as a refusal while the dialog was still up.
 *
 * There is no UI: with `Theme.JarvisInvisible` the user sees the system's own
 * permission dialog and nothing else.
 */
class PermissionRequestActivity : Activity() {

    private var requestId: String? = null
    private var wanted: List<String> = emptyList()
    private var settled = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val id = intent?.getStringExtra(PermissionBridge.EXTRA_REQUEST_ID)
        val asked = intent?.getStringArrayExtra(PermissionBridge.EXTRA_PERMISSIONS)?.toList()
        if (id.isNullOrEmpty() || asked.isNullOrEmpty()) {
            finish(); return
        }
        requestId = id
        wanted = asked

        // Already granted between the request being raised and this appearing —
        // the user may have tapped the notification long after fixing it in
        // Settings. Nothing to ask.
        val outstanding = RuntimePermissions.missing(this, asked)
        if (outstanding.isEmpty()) {
            settle(emptyList(), emptyList()); finish(); return
        }

        // A permission dialog on a locked phone is answered by whoever is
        // holding it, not by its owner. Report the refusal and leave the
        // notification for the user to tap once they are back in.
        if (isLocked()) {
            Log.i(TAG, "not asking for $outstanding while the phone is locked")
            // `keepNotification`: the sentence above is only true if the
            // notification survives. Without it the bridge's `finally` cancels
            // the one thing this branch exists to leave behind, and the user
            // unlocks the phone to find nothing at all.
            settle(outstanding, emptyList(), keepNotification = true); finish(); return
        }

        // A recreated instance has nothing to do and must not sit there.
        //
        // `configChanges` in the manifest absorbs rotation, so the only way to
        // arrive here with a bundle is a process death and restore — and the
        // instance that died already settled the request from its own
        // `onDestroy`. There is no deferred left to answer and no dialog left
        // to wait for. Returning without finishing (which this used to do, on
        // the false premise that the dialog was still up) left an invisible
        // activity alive in its own recents-excluded task, for good.
        if (savedInstanceState != null) {
            Log.i(TAG, "recreated after process death; the request is already settled")
            finish(); return
        }

        try {
            requestPermissions(outstanding.toTypedArray(), REQ_PERMISSIONS)
        } catch (t: Throwable) {
            Log.w(TAG, "could not raise the permission dialog", t)
            settle(outstanding, emptyList()); finish()
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_PERMISSIONS) { finish(); return }

        val stillMissing = permissions.filterIndexed { i, _ ->
            grantResults.getOrNull(i) != PackageManager.PERMISSION_GRANTED
        }
        // An empty grantResults means the request was cancelled — by the user
        // backing out, or by another dialog taking over. Nothing was decided,
        // so nothing is remembered as permanent.
        val permanent = if (grantResults.isEmpty()) {
            emptyList()
        } else {
            // No dialog will be shown for these again: the platform reports
            // "don't show a rationale" for a permission that was never asked
            // for AND for one refused with "don't ask again", and we know it
            // has just been asked.
            stillMissing.filterNot { runCatching { shouldShowRequestPermissionRationale(it) }.getOrDefault(true) }
        }
        settle(stillMissing, permanent)
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        // Killed, swiped away, or finished without an answer. Fail closed —
        // a no-op if the answer already arrived.
        requestId?.let { PermissionBridge.abandon(it, wanted) }
    }

    private fun settle(
        stillMissing: List<String>,
        permanent: List<String>,
        keepNotification: Boolean = false,
    ) {
        if (settled) return
        settled = true
        requestId?.let { PermissionBridge.deliver(it, stillMissing, permanent, keepNotification) }
    }

    private fun isLocked(): Boolean = try {
        getSystemService(KeyguardManager::class.java)?.isKeyguardLocked == true
    } catch (t: Throwable) {
        Log.w(TAG, "keyguard check failed; assuming locked", t)
        true
    }

    private companion object {
        const val TAG = "JarvisPermission"
        const val REQ_PERMISSIONS = 5301
    }
}
