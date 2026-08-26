package ai.jarvis.app

import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.builtin.NdefCodec
import ai.jarvis.app.automation.actions.builtin.NfcRead
import ai.jarvis.app.automation.actions.builtin.NfcWrite
import ai.jarvis.app.ui.ForegroundResultBridge
import android.app.Activity
import android.nfc.NdefMessage
import android.nfc.NdefRecord
import android.nfc.NfcAdapter
import android.nfc.Tag
import android.nfc.tech.Ndef
import android.nfc.tech.NdefFormatable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Toast

/**
 * One frame of Activity, so that an NFC tag can be read or written from a
 * command that has no Activity.
 *
 * Reader mode (`NfcAdapter.enableReaderMode`) is the one NFC API that hands a
 * tag to the app that asked rather than to whatever the system dispatches the
 * tag to, and it only exists on a resumed Activity. So: appear, arm the
 * reader, wait for one tag or the clock, report, disappear. The window itself
 * is invisible (`Theme.JarvisInvisible`); the person is told what to do by a
 * toast, and by whatever Jarvis said when it asked.
 *
 * The work on the tag happens on the reader callback's thread, which is a
 * binder thread the platform expects to block on I/O; nothing here touches a
 * view from it.
 */
class NfcTagActivity : Activity() {

    private var requestId: String? = null
    private var settled = false
    private var adapter: NfcAdapter? = null
    private val clock = Handler(Looper.getMainLooper())
    private val timeUp = Runnable {
        settle(ActionResult.error("no tag was presented within $timeoutS seconds"))
        finish()
    }
    private var timeoutS = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id = intent?.getStringExtra(ForegroundResultBridge.EXTRA_REQUEST_ID)
        if (id.isNullOrEmpty()) {
            finish(); return
        }
        requestId = id
        ForegroundResultBridge.raised(id)
        // Same reasoning as the other trampolines: a restored instance has
        // nothing to wait for and must not linger invisibly.
        if (savedInstanceState != null || !ForegroundResultBridge.isPending(id)) {
            finish(); return
        }
        adapter = NfcAdapter.getDefaultAdapter(this)
        if (adapter == null) {
            settle(ActionResult.error(NfcRead.NO_NFC)); finish(); return
        }
        timeoutS = intent.getIntExtra(EXTRA_TIMEOUT_S, NfcRead.DEFAULT_TIMEOUT_S)
        clock.postDelayed(timeUp, timeoutS * 1000L)
    }

    override fun onResume() {
        super.onResume()
        val nfc = adapter ?: return
        if (!nfc.isEnabled) {
            settle(ActionResult.error(NfcRead.NFC_OFF)); finish(); return
        }
        try {
            nfc.enableReaderMode(this, NfcAdapter.ReaderCallback { tag -> onTag(tag) }, READER_FLAGS, null)
            Toast.makeText(this, if (isWrite()) "Hold the tag to write against the back of the phone" else "Hold a tag against the back of the phone", Toast.LENGTH_LONG).show()
        } catch (t: Throwable) {
            Log.w(TAG, "could not enable reader mode", t)
            settle(ActionResult.error("could not switch NFC to reader mode: ${t.message ?: t.javaClass.simpleName}")); finish()
        }
    }

    override fun onPause() {
        runCatching { adapter?.disableReaderMode(this) }
        super.onPause()
    }

    override fun onDestroy() {
        clock.removeCallbacks(timeUp)
        super.onDestroy()
        requestId?.let { ForegroundResultBridge.abandon(it, "the NFC prompt") }
    }

    private fun isWrite(): Boolean = intent?.getStringExtra(EXTRA_MODE) == MODE_WRITE

    /** Reader-mode callback: one tag, one answer, then the window goes. */
    private fun onTag(tag: Tag) {
        val result = try {
            if (isWrite()) write(tag) else read(tag)
        } catch (t: Throwable) {
            Log.w(TAG, "tag i/o failed", t)
            ActionResult.error("the tag could not be ${if (isWrite()) "written" else "read"}: ${t.message ?: t.javaClass.simpleName}")
        }
        clock.post {
            settle(result)
            finish()
        }
    }

    private fun read(tag: Tag): ActionResult {
        val ndef = Ndef.get(tag)
            ?: return ActionResult.ok(NfcRead.describe(tag.id, tag.techList, records = emptyList(), ndef = false))
        val message = ndef.cachedNdefMessage ?: run {
            ndef.connect()
            try { ndef.ndefMessage } finally { runCatching { ndef.close() } }
        }
        val records = message?.records?.map { NfcRead.Record.of(it) }.orEmpty()
        return ActionResult.ok(NfcRead.describe(tag.id, tag.techList, records, ndef = true))
    }

    private fun write(tag: Tag): ActionResult {
        val text = intent?.getStringExtra(EXTRA_TEXT)
        val uri = intent?.getStringExtra(EXTRA_URI)
        val record = when {
            !text.isNullOrEmpty() -> NdefRecord(NdefRecord.TNF_WELL_KNOWN, NdefRecord.RTD_TEXT, ByteArray(0), NdefCodec.encodeText(text))
            !uri.isNullOrEmpty() -> NdefRecord(NdefRecord.TNF_WELL_KNOWN, NdefRecord.RTD_URI, ByteArray(0), NdefCodec.encodeUri(uri))
            else -> return ActionResult.error("nothing to write")
        }
        val message = NdefMessage(arrayOf(record))
        val size = message.byteArrayLength
        Ndef.get(tag)?.let { ndef ->
            ndef.connect()
            try {
                if (!ndef.isWritable) return ActionResult.error("this tag is read-only")
                if (ndef.maxSize < size) return ActionResult.error(NfcWrite.tooBig(size, ndef.maxSize))
                ndef.writeNdefMessage(message)
            } finally {
                runCatching { ndef.close() }
            }
            return ActionResult.ok(NfcWrite.written(tag.id, size, formatted = false, text = text, uri = uri))
        }
        NdefFormatable.get(tag)?.let { blank ->
            blank.connect()
            try {
                blank.format(message)
            } finally {
                runCatching { blank.close() }
            }
            return ActionResult.ok(NfcWrite.written(tag.id, size, formatted = true, text = text, uri = uri))
        }
        return ActionResult.error("this tag does not hold NDEF and cannot be formatted for it")
    }

    private fun settle(result: ActionResult) {
        if (settled) return
        settled = true
        clock.removeCallbacks(timeUp)
        requestId?.let { ForegroundResultBridge.deliver(it, result) }
    }

    companion object {
        const val EXTRA_MODE = "ai.jarvis.app.nfc.MODE"
        const val EXTRA_TIMEOUT_S = "ai.jarvis.app.nfc.TIMEOUT_S"
        const val EXTRA_TEXT = "ai.jarvis.app.nfc.TEXT"
        const val EXTRA_URI = "ai.jarvis.app.nfc.URI"
        const val MODE_READ = "read"
        const val MODE_WRITE = "write"
        private const val TAG = "JarvisNfc"

        /** Every tag technology reader mode can present; NDEF is read on top of whichever answers. */
        private const val READER_FLAGS = NfcAdapter.FLAG_READER_NFC_A or NfcAdapter.FLAG_READER_NFC_B or
            NfcAdapter.FLAG_READER_NFC_F or NfcAdapter.FLAG_READER_NFC_V
    }
}
