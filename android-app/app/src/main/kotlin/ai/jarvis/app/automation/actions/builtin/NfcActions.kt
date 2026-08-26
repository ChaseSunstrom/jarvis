package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.NfcTagActivity
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.intOr
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.markUntrusted
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.actions.toJsonArray
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.ui.ForegroundResultBridge
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.nfc.NdefRecord
import android.nfc.NfcAdapter
import org.json.JSONObject

/**
 * The NFC rows of the Tasker table (M61): read a tag, write a tag. Both go
 * through [NfcTagActivity] because reader mode only exists on a resumed
 * Activity; what lives here is the arithmetic — the NDEF text and URI record
 * encodings, the timeout bounds, the result shapes — so the JVM can prove it.
 */

/**
 * The two NDEF record types a phone owner writes by hand, encoded and decoded
 * byte for byte (NFC Forum RTD Text 1.0 and RTD URI 1.0).
 *
 * Written out rather than left to `NdefRecord.createTextRecord` / `toUri` for
 * one reason: those cannot run on the JVM, so the encoding a tag actually
 * receives would be the one thing about this feature no test had looked at.
 * `NdefRecord` is still what gets written; these produce its payload.
 */
object NdefCodec {

    /** The URI prefixes the spec abbreviates to one byte; the index is the byte. */
    val URI_PREFIXES: List<String> = listOf(
        "", "http://www.", "https://www.", "http://", "https://", "tel:", "mailto:",
        "ftp://anonymous:anonymous@", "ftp://ftp.", "ftps://", "sftp://", "smb://", "nfs://",
        "ftp://", "dav://", "news:", "telnet://", "imap:", "rtsp://", "urn:", "pop:", "sip:",
        "sips:", "tftp:", "btspp://", "btl2cap://", "btgoep://", "tcpobex://", "irdaobex://",
        "file://", "urn:epc:id:", "urn:epc:tag:", "urn:epc:pat:", "urn:epc:raw:", "urn:epc:",
        "urn:nfc:",
    )

    /** Status byte, then the language code, then UTF-8 text. */
    fun encodeText(text: String, lang: String = "en"): ByteArray {
        val language = lang.toByteArray(Charsets.US_ASCII).take(63).toByteArray()
        return byteArrayOf(language.size.toByte()) + language + text.toByteArray(Charsets.UTF_8)
    }

    /** The text of a Text record, or null when the payload is malformed. */
    fun decodeText(payload: ByteArray): String? {
        if (payload.isEmpty()) return null
        val status = payload[0].toInt()
        val langLength = status and 0x3F
        if (1 + langLength > payload.size) return null
        val charset = if (status and 0x80 != 0) Charsets.UTF_16 else Charsets.UTF_8
        return String(payload, 1 + langLength, payload.size - 1 - langLength, charset)
    }

    /** The language of a Text record ("en"), or null when malformed. */
    fun languageOf(payload: ByteArray): String? {
        if (payload.isEmpty()) return null
        val langLength = payload[0].toInt() and 0x3F
        if (1 + langLength > payload.size) return null
        return String(payload, 1, langLength, Charsets.US_ASCII)
    }

    /** The longest abbreviation that fits, as one byte, then the rest of the URI. */
    fun encodeUri(uri: String): ByteArray {
        var code = 0
        for (i in 1 until URI_PREFIXES.size) {
            val prefix = URI_PREFIXES[i]
            if (uri.startsWith(prefix) && prefix.length > URI_PREFIXES[code].length) code = i
        }
        return byteArrayOf(code.toByte()) + uri.substring(URI_PREFIXES[code].length).toByteArray(Charsets.UTF_8)
    }

    /** The URI of a URI record, or null when the payload is malformed or uses a code the spec reserves. */
    fun decodeUri(payload: ByteArray): String? {
        if (payload.isEmpty()) return null
        val code = payload[0].toInt() and 0xFF
        if (code >= URI_PREFIXES.size) return null
        return URI_PREFIXES[code] + String(payload, 1, payload.size - 1, Charsets.UTF_8)
    }

    /** A tag id as the hex people compare on the back of a sticker. */
    fun hex(bytes: ByteArray?): String? = bytes?.takeIf { it.isNotEmpty() }?.joinToString("") { "%02x".format(it) }

    /** `android.nfc.tech.NfcA` as `NfcA` — the class names are the platform's, the words are what a person recognises. */
    fun techNames(techList: Array<String>?): List<String> = techList.orEmpty().map { it.substringAfterLast('.') }
}

/** Shared bounds for how long the phone waits with the reader armed. */
internal object NfcTimeout {
    const val MIN_S = 5
    const val MAX_S = 120

    /** Seconds to wait, or null when the parameter is present and outside the bounds. */
    fun of(params: JSONObject, default: Int): Int? {
        if (!params.has("timeout_s")) return default
        return params.intOr("timeout_s", -1).takeIf { it in MIN_S..MAX_S }
    }

    const val OUT_OF_RANGE = "timeout_s must be between $MIN_S and $MAX_S"
}

/** Tier 2 — read one tag. Asks once; what is read is somebody else's bytes. */
object NfcRead : JarvisAction {
    override val id = "nfc_read"
    override val tier = ActionTier.NOTIFY
    override val description = "Read the next NFC tag held to the back of the phone: its id, its technologies, and any NDEF text or URI records. Waits a few seconds for a tag."
    override val paramsSchema = mapOf("timeout_s" to "int ${NfcTimeout.MIN_S}-${NfcTimeout.MAX_S} (optional): how long to wait for a tag (default $DEFAULT_TIMEOUT_S)")
    override val capability = "nfc"

    /** A tag's records were written by whoever wrote the tag. */
    override val untrustedOutput = true
    override val timeoutMs: Long = (NfcTimeout.MAX_S + 15) * 1000L

    override fun isAvailable(ctx: Context): Boolean = ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_NFC)
    override val unsupportedReason: String get() = NO_NFC

    /** One record, reduced to what a person or a model can use. */
    data class Record(val type: String, val text: String? = null, val uri: String? = null, val mime: String? = null, val language: String? = null, val bytes: Int = 0) {
        fun toJson(): JSONObject = json("type" to type, "text" to text, "uri" to uri, "mime" to mime, "language" to language, "bytes" to bytes)

        companion object {
            /** Classify a record by TNF and type bytes — the same arithmetic for a real [NdefRecord] and for a test. */
            fun classify(tnf: Short, type: ByteArray, payload: ByteArray): Record = when {
                tnf == NdefRecord.TNF_WELL_KNOWN && type.contentEquals(RTD_TEXT) ->
                    Record("text", text = NdefCodec.decodeText(payload), language = NdefCodec.languageOf(payload), bytes = payload.size)
                tnf == NdefRecord.TNF_WELL_KNOWN && type.contentEquals(RTD_URI) ->
                    Record("uri", uri = NdefCodec.decodeUri(payload), bytes = payload.size)
                tnf == NdefRecord.TNF_ABSOLUTE_URI ->
                    Record("uri", uri = String(type, Charsets.UTF_8), bytes = payload.size)
                tnf == NdefRecord.TNF_MIME_MEDIA ->
                    Record("mime", mime = String(type, Charsets.US_ASCII), text = payload.toString(Charsets.UTF_8).takeIf { isText(payload) }, bytes = payload.size)
                tnf == NdefRecord.TNF_EMPTY -> Record("empty")
                else -> Record("other", bytes = payload.size)
            }

            fun of(record: NdefRecord): Record = classify(record.tnf, record.type ?: ByteArray(0), record.payload ?: ByteArray(0))

            /** Whether a MIME payload is worth returning as text: no control bytes but the usual whitespace. */
            fun isText(payload: ByteArray): Boolean =
                payload.isNotEmpty() && payload.all { b -> b.toInt() and 0xFF >= 0x20 || b == '\n'.code.toByte() || b == '\r'.code.toByte() || b == '\t'.code.toByte() }

            // The RTD type bytes, spelled out so a JVM test needs no android.jar
            // behind `NdefRecord.RTD_TEXT` (the stub returns null for it).
            val RTD_TEXT: ByteArray = byteArrayOf('T'.code.toByte())
            val RTD_URI: ByteArray = byteArrayOf('U'.code.toByte())
        }
    }

    /** The result for one tag; marked untrusted because every record is third-party bytes. */
    fun describe(tagId: ByteArray?, techList: Array<String>?, records: List<Record>, ndef: Boolean): JSONObject =
        json(
            "tag_id" to NdefCodec.hex(tagId),
            "tech" to NdefCodec.techNames(techList).toJsonArray(),
            "ndef" to ndef,
            "records" to records.map { it.toJson() }.toJsonArray(),
            "text" to records.firstNotNullOfOrNull { it.text },
            "uri" to records.firstNotNullOfOrNull { it.uri },
        ).markUntrusted()

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val timeout = NfcTimeout.of(params, DEFAULT_TIMEOUT_S) ?: return ActionResult.error(NfcTimeout.OUT_OF_RANGE)
        val adapter = NfcAdapter.getDefaultAdapter(ctx) ?: return ActionResult.error(NO_NFC)
        if (!adapter.isEnabled) return ActionResult.error(NFC_OFF)
        val intent = Intent(ctx, NfcTagActivity::class.java)
            .putExtra(NfcTagActivity.EXTRA_MODE, NfcTagActivity.MODE_READ)
            .putExtra(NfcTagActivity.EXTRA_TIMEOUT_S, timeout)
        return ForegroundResultBridge.run(ctx, intent, "the NFC prompt", timeout * 1000L)
    }

    const val DEFAULT_TIMEOUT_S = 30
    const val NO_NFC = "this phone has no NFC"
    const val NFC_OFF = "NFC is switched off; turn it on in Settings (open_settings_panel with panel nfc)"
}

/** Tier 2 — write one record to a tag. Asks once; the person still has to hold the tag there. */
object NfcWrite : JarvisAction {
    override val id = "nfc_write"
    override val tier = ActionTier.NOTIFY
    override val description = "Write one NDEF record — a text or a URI — to the next tag held to the back of the phone. Replaces what the tag held. Waits a few seconds for a tag."
    override val paramsSchema = mapOf(
        "text" to "string: the text to write (at most $MAX_CHARS characters) — or",
        "uri" to "string: the URI to write (https://…, tel:…, mailto:…)",
        "timeout_s" to "int ${NfcTimeout.MIN_S}-${NfcTimeout.MAX_S} (optional): how long to wait for a tag (default ${NfcRead.DEFAULT_TIMEOUT_S})",
    )
    override val capability = "nfc"
    override val timeoutMs: Long = (NfcTimeout.MAX_S + 15) * 1000L

    override fun isAvailable(ctx: Context): Boolean = ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_NFC)
    override val unsupportedReason: String get() = NfcRead.NO_NFC

    /** What would be written, validated, or the reason it will not be. */
    data class Payload(val text: String?, val uri: String?)

    fun payloadOf(params: JSONObject): Pair<Payload?, String?> {
        val text = params.str("text")
        val uri = params.str("uri")
        return when {
            text == null && uri == null -> null to "text or uri is required"
            text != null && uri != null -> null to "write either text or uri, not both"
            text != null && text.length > MAX_CHARS -> null to "text is too long (at most $MAX_CHARS characters)"
            uri != null && !uri.contains(':') -> null to "uri must have a scheme, e.g. https://… or tel:…"
            uri != null && uri.any { it.isWhitespace() || it.isISOControl() } -> null to "uri must not contain spaces or control characters"
            else -> Payload(text, uri) to null
        }
    }

    fun written(tagId: ByteArray?, bytes: Int, formatted: Boolean, text: String?, uri: String?): JSONObject =
        json("tag_id" to NdefCodec.hex(tagId), "bytes" to bytes, "formatted" to formatted, "text" to text, "uri" to uri)

    fun tooBig(needed: Int, capacity: Int): String = "the record needs $needed bytes and this tag holds $capacity"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val (payload, error) = payloadOf(params)
        if (payload == null) return ActionResult.error(error ?: "nothing to write")
        val timeout = NfcTimeout.of(params, NfcRead.DEFAULT_TIMEOUT_S) ?: return ActionResult.error(NfcTimeout.OUT_OF_RANGE)
        val adapter = NfcAdapter.getDefaultAdapter(ctx) ?: return ActionResult.error(NfcRead.NO_NFC)
        if (!adapter.isEnabled) return ActionResult.error(NfcRead.NFC_OFF)
        val intent = Intent(ctx, NfcTagActivity::class.java)
            .putExtra(NfcTagActivity.EXTRA_MODE, NfcTagActivity.MODE_WRITE)
            .putExtra(NfcTagActivity.EXTRA_TIMEOUT_S, timeout)
        payload.text?.let { intent.putExtra(NfcTagActivity.EXTRA_TEXT, it) }
        payload.uri?.let { intent.putExtra(NfcTagActivity.EXTRA_URI, it) }
        return ForegroundResultBridge.run(ctx, intent, "the NFC prompt", timeout * 1000L)
    }

    /** A Type 2 sticker holds a few hundred bytes; anything a voice assistant writes fits well inside this. */
    const val MAX_CHARS = 2000
}
