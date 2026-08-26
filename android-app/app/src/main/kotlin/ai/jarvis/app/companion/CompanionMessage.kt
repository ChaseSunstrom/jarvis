package ai.jarvis.app.companion

import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

/**
 * The cross-device conversation wire format, in one file, so it can be reviewed
 * against `docs/cross-device.md` without reading any UI code.
 *
 * Server -> device:
 * ```json
 * {"type": "jarvis_message", "message_id": "a1b2c3", "kind": "ask",
 *  "mode": "ask", "text": "Deploy to production?", "options": ["yes", "no"],
 *  "conversation_id": "conv-7", "importance": "high", "timeout_s": 120,
 *  "spoken": false}
 * ```
 *
 * `spoken` (M66) says the words are already being said to the user by the
 * reply they belong to — a question raised by a spoken turn. Like `mode` it is
 * a PRESENTATION hint: show, do not read out. It cannot make the phone do
 * anything, and an absent or garbled value is `false`, which is the louder
 * reading.
 *
 * Device -> server:
 * ```json
 * {"type": "jarvis_message_result", "message_id": "a1b2c3",
 *  "status": "answered", "answer": "no"}
 * ```
 *
 * No Android imports (org.json only), so the shapes stay reviewable and the
 * mirror in `tools/presence_signals_test.py` can check them.
 *
 * Parsing rule, identical to [ai.jarvis.app.channel.ChannelFrames]: **read the
 * fields we know, ignore the rest, and never let an unknown field change
 * behaviour.** A server that adds `"run": "unlock_door"` or `"tier": 1` to a
 * `jarvis_message` is describing a field this parser does not have and will not
 * grow. A proactive message is information and questions only; acting on an
 * answer still goes back through `device_command` and the full policy path, so
 * an answer of "yes" is data, never an authorisation token.
 */
object CompanionProtocol {

    const val TYPE_MESSAGE = "jarvis_message"
    const val TYPE_RESULT = "jarvis_message_result"

    /** The four statuses. There is no fifth, and no "partial". */
    const val STATUS_ANSWERED = "answered"
    const val STATUS_DISMISSED = "dismissed"
    const val STATUS_TIMEOUT = "timeout"
    const val STATUS_UNDELIVERABLE = "undeliverable"

    val VALID_STATUSES = setOf(
        STATUS_ANSWERED, STATUS_DISMISSED, STATUS_TIMEOUT, STATUS_UNDELIVERABLE
    )

    const val MODE_SPEAK = "speak"
    const val MODE_ASK = "ask"
    const val MODE_NOTIFY = "notify"

    val VALID_MODES = setOf(MODE_SPEAK, MODE_ASK, MODE_NOTIFY)

    const val KIND_ASK = "ask"

    /** `kind` -> the mode used when the server sent no usable `mode`. */
    private val KIND_TO_MODE = mapOf(
        "say" to MODE_SPEAK,
        KIND_ASK to MODE_ASK,
        "notify" to MODE_NOTIFY
    )

    val VALID_IMPORTANCE = setOf("low", "normal", "high", "critical")

    /** Importance levels whose text is not shown over the keyguard. */
    val SENSITIVE_IMPORTANCE = setOf("high", "critical")

    // Clamps. Everything here is server-supplied, so nothing is unbounded.
    const val MAX_ID = 128
    const val MAX_TEXT = 4000
    const val MAX_OPTIONS = 8
    const val MAX_OPTION_LEN = 80

    const val MIN_TIMEOUT_MS = 5_000L
    const val MAX_TIMEOUT_MS = 600_000L
    const val DEFAULT_ASK_TIMEOUT_MS = 120_000L
    const val DEFAULT_QUIET_TIMEOUT_MS = 30_000L

    /**
     * A parsed `jarvis_message`. Note what is NOT here: no action, no params,
     * no tier, no "remember this". A struct with no slot for them cannot be
     * talked into one, however the server words the frame.
     */
    data class Message(
        val messageId: String,
        val kind: String,
        /** One of [VALID_MODES], or "" when the server sent nothing usable. */
        val mode: String,
        /** UNTRUSTED display text. Rendered as text and nothing else. */
        val text: String,
        val options: List<String> = emptyList(),
        val conversationId: String? = null,
        val importance: String = "normal",
        val timeoutMs: Long = DEFAULT_ASK_TIMEOUT_MS,
        /**
         * The reply that carries this question is being read aloud by the
         * surface the user spoke to, so this phone shows it and does not say
         * it again. The operator heard every question twice before this.
         */
        val spoken: Boolean = false,
    ) {
        val wantsAnswer: Boolean get() = mode == MODE_ASK

        /** True for the messages whose text stays hidden on a locked phone. */
        val sensitive: Boolean
            get() = importance in CompanionProtocol.SENSITIVE_IMPORTANCE
    }

    /**
     * Null when the frame is not a usable `jarvis_message`.
     *
     * The only fatal defect is a missing `message_id`: without one there is
     * nothing to answer and nothing the server could match a reply to, so the
     * frame is dropped rather than guessed at. Everything else is clamped into
     * something answerable — a truncated question the user can answer beats a
     * silent drop.
     */
    fun parse(msg: JSONObject): Message? {
        if (msg.optString("type") != TYPE_MESSAGE) return null
        val messageId = msg.optString("message_id").trim().take(MAX_ID)
        if (messageId.isEmpty()) return null

        val kind = msg.optString("kind").trim().lowercase(Locale.ROOT)
        val rawMode = msg.optString("mode").trim().lowercase(Locale.ROOT)
        // The server's routing decision is the authority; when it is missing or
        // garbled, fall back to what the message *is*.
        val routed = if (rawMode in VALID_MODES) rawMode else KIND_TO_MODE[kind].orEmpty()
        // ...with one exception, and it is the load-bearing one. The server
        // picks the PRESENTATION; it does not get to turn a question into
        // something this phone acknowledges on the user's behalf. `notify` and
        // `speak` both end in a `answered` result — that is how a device says
        // "delivered" — and for a `kind: ask` the server reads that as a reply
        // nobody gave: it resolves the waiting `companion.ask` with an empty
        // answer AND stops escalating, so the question reaches the user on no
        // device at all. jarvis-core produced exactly that frame for a critical
        // message when nothing was reachable. Only a human answers a question.
        val mode = if (kind == KIND_ASK && routed != MODE_ASK) MODE_ASK else routed

        val importance = msg.optString("importance").trim().lowercase(Locale.ROOT)
            .takeIf { it in VALID_IMPORTANCE } ?: "normal"

        val conversationId = msg.optString("conversation_id").trim().take(MAX_ID)
            .takeIf { it.isNotEmpty() }

        val defaultTimeout =
            if (mode == MODE_ASK) DEFAULT_ASK_TIMEOUT_MS else DEFAULT_QUIET_TIMEOUT_MS

        return Message(
            messageId = messageId,
            kind = kind.ifEmpty { "notify" },
            mode = mode,
            text = msg.optString("text").take(MAX_TEXT),
            options = parseOptions(msg.optJSONArray("options")),
            conversationId = conversationId,
            importance = importance,
            timeoutMs = clampTimeout(if (msg.isNull("timeout_s")) null else msg.opt("timeout_s"),
                defaultTimeout),
            spoken = msg.optBoolean("spoken", false),
        )
    }

    /** Seconds on the wire, milliseconds in the app. Clamped, never trusted. */
    fun clampTimeout(rawSeconds: Any?, default: Long): Long {
        val seconds = when (rawSeconds) {
            is Number -> rawSeconds.toDouble()
            is String -> rawSeconds.trim().toDoubleOrNull() ?: return default
            else -> return default
        }
        if (seconds.isNaN() || seconds <= 0.0) return default
        val millis = (seconds * 1000.0).toLong()
        return millis.coerceIn(MIN_TIMEOUT_MS, MAX_TIMEOUT_MS)
    }

    private fun parseOptions(array: JSONArray?): List<String> {
        if (array == null) return emptyList()
        val out = ArrayList<String>(minOf(array.length(), MAX_OPTIONS))
        for (i in 0 until array.length()) {
            if (out.size >= MAX_OPTIONS) break
            val raw = array.opt(i) ?: continue
            if (raw is JSONObject || raw is JSONArray) continue
            val text = raw.toString().trim().replace('\n', ' ').take(MAX_OPTION_LEN)
            if (text.isNotEmpty() && text !in out) out.add(text)
        }
        return out
    }

    /**
     * The one wire shape this module produces.
     *
     * An unrecognised status becomes `undeliverable`: a garbled answer must
     * never read as `answered`, which is the only status that stops the server
     * escalating to another device.
     */
    fun result(messageId: String, status: String, answer: String? = null): JSONObject {
        val clean = status.trim().lowercase(Locale.ROOT)
            .takeIf { it in VALID_STATUSES } ?: STATUS_UNDELIVERABLE
        val out = JSONObject()
            .put("type", TYPE_RESULT)
            .put("message_id", messageId)
            .put("status", clean)
        // Only `answered` carries an answer; the others must not smuggle one.
        if (clean == STATUS_ANSWERED) out.put("answer", (answer ?: "").take(MAX_TEXT))
        return out
    }
}
