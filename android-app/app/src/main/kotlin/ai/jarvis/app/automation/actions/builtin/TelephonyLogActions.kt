package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.TimeParse
import ai.jarvis.app.automation.actions.granted
import ai.jarvis.app.automation.actions.intOr
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.markUntrusted
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.database.Cursor
import android.provider.CallLog
import android.provider.Telephony
import android.telecom.TelecomManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * The phone rows of the Tasker table that read what other people sent or did
 * (M61): the inbox, the call log, and hanging up.
 *
 * Every read here is Tier 3 and every result is untrusted. A message body is
 * the most attacker-reachable text on a phone — anyone with the number can
 * put words in it — so it is asked about every time and never allowed to
 * drive a later step on its own. The selections are pure functions so the
 * JVM can prove what a filter means before a provider sees it.
 */

/** The `limit` every log read takes: a handful by default, never the whole provider. */
internal object LogLimit {
    const val DEFAULT = 10
    const val MAX = 50

    fun of(params: JSONObject): Int = params.intOr("limit", DEFAULT).coerceIn(1, MAX)
}

/** Tier 3 — read messages. Every time, and what is read is somebody else's words. */
object ReadSms : JarvisAction {
    override val id = "read_sms"
    override val tier = ActionTier.CONFIRM
    override val description = "Read recent SMS messages: the newest first, optionally only from one number or after a time. Asks every time."
    override val paramsSchema = mapOf(
        "limit" to "int 1-${LogLimit.MAX} (optional): how many (default ${LogLimit.DEFAULT})",
        "box" to "string (optional): inbox (default) | sent | all",
        "from" to "string (optional): only messages whose number contains this",
        "since" to "string (optional): only messages after this time (ISO-8601 or epoch millis)",
    )
    override val capability = "sms"
    override val requiredPermissions = listOf(Manifest.permission.READ_SMS)

    /** A message body is whatever the sender typed. */
    override val untrustedOutput = true

    override fun isAvailable(ctx: Context): Boolean =
        ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)

    /** The box word as the provider's `type`, or null for all; the second half is an error for a word that is none. */
    fun boxOf(raw: String?): Pair<Int?, String?> = when (raw?.trim()?.lowercase().orEmpty().ifEmpty { "inbox" }) {
        "inbox", "received" -> Telephony.Sms.MESSAGE_TYPE_INBOX to null
        "sent" -> Telephony.Sms.MESSAGE_TYPE_SENT to null
        "all", "any" -> null to null
        else -> null to "box must be inbox, sent or all"
    }

    /** The provider's `type` in a word. */
    fun directionOf(type: Int): String = when (type) {
        Telephony.Sms.MESSAGE_TYPE_INBOX -> "received"
        Telephony.Sms.MESSAGE_TYPE_SENT -> "sent"
        Telephony.Sms.MESSAGE_TYPE_DRAFT -> "draft"
        Telephony.Sms.MESSAGE_TYPE_OUTBOX, Telephony.Sms.MESSAGE_TYPE_QUEUED -> "outgoing"
        Telephony.Sms.MESSAGE_TYPE_FAILED -> "failed"
        else -> "other"
    }

    /** The WHERE clause and its arguments for a box, a sender fragment and a lower time bound — or none. */
    fun selectionOf(type: Int?, from: String?, sinceMs: Long?): Pair<String?, Array<String>?> {
        val clauses = ArrayList<String>()
        val args = ArrayList<String>()
        if (type != null) { clauses += "${Telephony.Sms.TYPE} = ?"; args += type.toString() }
        if (!from.isNullOrBlank()) { clauses += "${Telephony.Sms.ADDRESS} LIKE ?"; args += "%${from.trim()}%" }
        if (sinceMs != null) { clauses += "${Telephony.Sms.DATE} >= ?"; args += sinceMs.toString() }
        if (clauses.isEmpty()) return null to null
        return clauses.joinToString(" AND ") to args.toTypedArray()
    }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult = withContext(Dispatchers.IO) {
        val (type, boxError) = boxOf(params.str("box"))
        if (boxError != null) return@withContext ActionResult.error(boxError)
        val since = params.str("since")?.let {
            TimeParse.epochMillis(it, System.currentTimeMillis()) ?: return@withContext ActionResult.error("since is not a time I understand: $it")
        }
        if (!ctx.granted(Manifest.permission.READ_SMS)) return@withContext ActionResult.missingPermission(Manifest.permission.READ_SMS)
        val limit = LogLimit.of(params)
        val (where, args) = selectionOf(type, params.str("from"), since)
        val projection = arrayOf(Telephony.Sms._ID, Telephony.Sms.ADDRESS, Telephony.Sms.BODY, Telephony.Sms.DATE, Telephony.Sms.TYPE, Telephony.Sms.READ)
        val messages = JSONArray()
        try {
            ctx.contentResolver.query(Telephony.Sms.CONTENT_URI, projection, where, args, "${Telephony.Sms.DATE} DESC")?.use { c ->
                while (c.moveToNext() && messages.length() < limit) {
                    messages.put(
                        json(
                            "id" to c.longOf(Telephony.Sms._ID),
                            "number" to c.stringOf(Telephony.Sms.ADDRESS),
                            "body" to c.stringOf(Telephony.Sms.BODY),
                            "date" to c.longOf(Telephony.Sms.DATE),
                            "direction" to directionOf(c.intOf(Telephony.Sms.TYPE) ?: -1),
                            "read" to ((c.intOf(Telephony.Sms.READ) ?: 1) != 0),
                        )
                    )
                }
            }
        } catch (e: SecurityException) {
            return@withContext ActionResult.missingPermission(Manifest.permission.READ_SMS)
        } catch (e: Exception) {
            return@withContext ActionResult.error("the messages could not be read: ${e.message ?: e.javaClass.simpleName}")
        }
        ActionResult.ok(json("messages" to messages, "count" to messages.length(), "box" to (params.str("box") ?: "inbox")).markUntrusted())
    }
}

/** Tier 3 — the call log: who rang, who was rung, when, for how long. */
object ReadCallLog : JarvisAction {
    override val id = "read_call_log"
    override val tier = ActionTier.CONFIRM
    override val description = "Read recent calls from the call log — number, name if the phone knows it, incoming/outgoing/missed, when, and how long. Asks every time."
    override val paramsSchema = mapOf(
        "limit" to "int 1-${LogLimit.MAX} (optional): how many (default ${LogLimit.DEFAULT})",
        "type" to "string (optional): all (default) | incoming | outgoing | missed | rejected | blocked | voicemail",
        "since" to "string (optional): only calls after this time (ISO-8601 or epoch)",
    )
    override val capability = "telephony"
    override val requiredPermissions = listOf(Manifest.permission.READ_CALL_LOG)

    /** The cached name beside a number is whatever the contact or the carrier supplied. */
    override val untrustedOutput = true

    override fun isAvailable(ctx: Context): Boolean =
        ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)

    /** A type word as the provider's constant, or null for all; the second half is an error for a word that is none. */
    fun typeOf(raw: String?): Pair<Int?, String?> = when (raw?.trim()?.lowercase().orEmpty().ifEmpty { "all" }) {
        "all", "any" -> null to null
        "incoming", "received" -> CallLog.Calls.INCOMING_TYPE to null
        "outgoing", "dialled", "dialed" -> CallLog.Calls.OUTGOING_TYPE to null
        "missed" -> CallLog.Calls.MISSED_TYPE to null
        "rejected", "declined" -> CallLog.Calls.REJECTED_TYPE to null
        "blocked" -> CallLog.Calls.BLOCKED_TYPE to null
        "voicemail" -> CallLog.Calls.VOICEMAIL_TYPE to null
        else -> null to "type must be all, incoming, outgoing, missed, rejected, blocked or voicemail"
    }

    /** The provider's constant in a word. */
    fun nameOfType(type: Int): String = when (type) {
        CallLog.Calls.INCOMING_TYPE -> "incoming"
        CallLog.Calls.OUTGOING_TYPE -> "outgoing"
        CallLog.Calls.MISSED_TYPE -> "missed"
        CallLog.Calls.REJECTED_TYPE -> "rejected"
        CallLog.Calls.BLOCKED_TYPE -> "blocked"
        CallLog.Calls.VOICEMAIL_TYPE -> "voicemail"
        CallLog.Calls.ANSWERED_EXTERNALLY_TYPE -> "answered elsewhere"
        else -> "other"
    }

    /** The WHERE clause and its arguments for a type and a lower time bound — or none. */
    fun selectionOf(type: Int?, sinceMs: Long?): Pair<String?, Array<String>?> {
        val clauses = ArrayList<String>()
        val args = ArrayList<String>()
        if (type != null) { clauses += "${CallLog.Calls.TYPE} = ?"; args += type.toString() }
        if (sinceMs != null) { clauses += "${CallLog.Calls.DATE} >= ?"; args += sinceMs.toString() }
        if (clauses.isEmpty()) return null to null
        return clauses.joinToString(" AND ") to args.toTypedArray()
    }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult = withContext(Dispatchers.IO) {
        val (type, typeError) = typeOf(params.str("type"))
        if (typeError != null) return@withContext ActionResult.error(typeError)
        val since = params.str("since")?.let {
            TimeParse.epochMillis(it, System.currentTimeMillis()) ?: return@withContext ActionResult.error("since is not a time I understand: $it")
        }
        if (!ctx.granted(Manifest.permission.READ_CALL_LOG)) return@withContext ActionResult.missingPermission(Manifest.permission.READ_CALL_LOG)
        val limit = LogLimit.of(params)
        val (where, args) = selectionOf(type, since)
        val projection = arrayOf(CallLog.Calls._ID, CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME, CallLog.Calls.TYPE, CallLog.Calls.DATE, CallLog.Calls.DURATION)
        val calls = JSONArray()
        try {
            ctx.contentResolver.query(CallLog.Calls.CONTENT_URI, projection, where, args, "${CallLog.Calls.DATE} DESC")?.use { c ->
                while (c.moveToNext() && calls.length() < limit) {
                    calls.put(
                        json(
                            "id" to c.longOf(CallLog.Calls._ID),
                            "number" to c.stringOf(CallLog.Calls.NUMBER),
                            "name" to c.stringOf(CallLog.Calls.CACHED_NAME),
                            "type" to nameOfType(c.intOf(CallLog.Calls.TYPE) ?: -1),
                            "date" to c.longOf(CallLog.Calls.DATE),
                            "duration_s" to c.longOf(CallLog.Calls.DURATION),
                        )
                    )
                }
            }
        } catch (e: SecurityException) {
            return@withContext ActionResult.missingPermission(Manifest.permission.READ_CALL_LOG)
        } catch (e: Exception) {
            return@withContext ActionResult.error("the call log could not be read: ${e.message ?: e.javaClass.simpleName}")
        }
        ActionResult.ok(json("calls" to calls, "count" to calls.length()).markUntrusted())
    }
}

/** Tier 3 — hangs up on a person, so it is confirmed every time, like dialling them. */
object EndCall : JarvisAction {
    override val id = "end_call"
    override val tier = ActionTier.CONFIRM
    override val description = "End the call in progress (hang up). Asks every time."
    override val paramsSchema = emptyMap<String, String>()
    override val capability = "telephony"
    override val requiredPermissions = listOf(Manifest.permission.ANSWER_PHONE_CALLS)

    override fun isAvailable(ctx: Context): Boolean =
        ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)

    /** What `TelecomManager.endCall` answered, as a result: false is "nothing to hang up", not a failure of the phone. */
    fun resultOf(ended: Boolean): ActionResult =
        if (ended) ActionResult.ok(json("ended" to true)) else ActionResult.error(NO_CALL)

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (!ctx.granted(Manifest.permission.ANSWER_PHONE_CALLS)) {
            return ActionResult.missingPermission(Manifest.permission.ANSWER_PHONE_CALLS)
        }
        val tm = ctx.getSystemService(TelecomManager::class.java) ?: return ActionResult.error("no telecom service")
        return try {
            // Deprecated from API 29 in favour of an InCallService, which is a
            // whole dialler; for "hang up" it is still the sanctioned call and
            // still works with ANSWER_PHONE_CALLS.
            @Suppress("DEPRECATION")
            resultOf(tm.endCall())
        } catch (e: SecurityException) {
            ActionResult.missingPermission(Manifest.permission.ANSWER_PHONE_CALLS)
        }
    }

    const val NO_CALL = "there is no call to end"
}

// Cursor helpers: a missing column is null, never a crash on a provider that
// lacks it (some OEM SMS providers do).
private fun Cursor.stringOf(column: String): String? =
    getColumnIndex(column).takeIf { it >= 0 }?.let { if (isNull(it)) null else getString(it) }

private fun Cursor.longOf(column: String): Long? =
    getColumnIndex(column).takeIf { it >= 0 }?.let { if (isNull(it)) null else getLong(it) }

private fun Cursor.intOf(column: String): Int? =
    getColumnIndex(column).takeIf { it >= 0 }?.let { if (isNull(it)) null else getInt(it) }
