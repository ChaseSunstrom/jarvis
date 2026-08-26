package ai.jarvis.app

import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.builtin.ScanCode
import ai.jarvis.app.ui.ForegroundResultBridge
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.os.Bundle
import android.util.Log

/**
 * One frame of Activity, so that a barcode can be scanned from a command that
 * has no Activity.
 *
 * Jarvis bundles no decoder (`docs/TOOLING_DECISIONS.md`): the scan is done by
 * whichever scanner app answers `com.google.zxing.client.android.SCAN` —
 * Binary Eye and QR Scanner on F-Droid do — and its answer comes back through
 * `onActivityResult`, which only an Activity has. This is the
 * [PermissionRequestActivity] shape: appear, hand off, report, disappear.
 *
 * Deliberately not `noHistory`: the scanner is a separate activity, so a
 * `noHistory` host would be finished the moment it lost the foreground and the
 * result would land on a dead window — the same trap the permission trampoline
 * documents.
 */
class ScanCodeActivity : Activity() {

    private var requestId: String? = null
    private var settled = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id = intent?.getStringExtra(ForegroundResultBridge.EXTRA_REQUEST_ID)
        if (id.isNullOrEmpty()) {
            finish(); return
        }
        requestId = id
        ForegroundResultBridge.raised(id)

        // A recreated instance (process death and restore) has no scanner to
        // wait for: the instance that died already settled the request from
        // its own onDestroy. Sitting here would leave an invisible activity
        // alive in its own task for good.
        if (savedInstanceState != null || !ForegroundResultBridge.isPending(id)) {
            finish(); return
        }

        val scan = Intent(ScanCode.SCAN_ACTION).putExtra("SAVE_HISTORY", false)
        intent?.getStringExtra(EXTRA_SCAN_MODE)?.let { scan.putExtra("SCAN_MODE", it) }
        try {
            startActivityForResult(scan, REQ_SCAN)
        } catch (e: ActivityNotFoundException) {
            settle(ActionResult.error(ScanCode.NO_SCANNER)); finish()
        } catch (t: Throwable) {
            Log.w(TAG, "could not start the scanner", t)
            settle(ActionResult.error("could not open the scanner: ${t.message ?: t.javaClass.simpleName}")); finish()
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_SCAN) { finish(); return }
        settle(
            ScanCode.resultOf(
                cancelled = resultCode != RESULT_OK,
                text = data?.getStringExtra("SCAN_RESULT"),
                format = data?.getStringExtra("SCAN_RESULT_FORMAT"),
            )
        )
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        // Killed or backed out of without an answer. Fail closed; a no-op once
        // the answer has been delivered.
        requestId?.let { ForegroundResultBridge.abandon(it, "the barcode scanner") }
    }

    private fun settle(result: ActionResult) {
        if (settled) return
        settled = true
        requestId?.let { ForegroundResultBridge.deliver(it, result) }
    }

    companion object {
        const val EXTRA_SCAN_MODE = "ai.jarvis.app.scan.MODE"
        private const val TAG = "JarvisScan"
        private const val REQ_SCAN = 5302
    }
}
