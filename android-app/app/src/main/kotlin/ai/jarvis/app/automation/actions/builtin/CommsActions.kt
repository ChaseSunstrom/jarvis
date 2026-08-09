package ai.jarvis.app.automation.actions.builtin

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.ContactsContract
import android.telephony.SmsManager
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.granted
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.markUntrusted
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * Anything that reaches another human being is Tier 3, every time, with the
 * real number and the real message body on screen. `read_contacts` is Tier 2
 * because it is read-only but is other people's personal data.
 */

/** Shared phone-number sanity checks. Pure enough to reason about in review. */
internal object PhoneNumbers {

    private val ALLOWED = Regex("^[+0-9 ()\\-.]{3,25}$")

    /** Emergency services — never dialled by an automation. */
    private val EMERGENCY = setOf(
        "911", "112", "999", "000", "111", "110", "119", "118", "102", "108", "115", "122"
    )

    fun isPlausible(number: String): Boolean {
        if (!ALLOWED.matches(number)) return false
        val digits = number.count { it.isDigit() }
        return digits in 3..20
    }

    fun isEmergency(number: String): Boolean {
        val digits = number.filter { it.isDigit() }
        return digits in EMERGENCY
    }
}

/** Tier 3 — messages another person. Confirmed every single time. */
object SendSms : JarvisAction {
    override val id = "send_sms"
    override val tier = ActionTier.CONFIRM
    override val description = "Send an SMS text message."
    override val paramsSchema = mapOf(
        "number" to "string: destination phone number",
        "body" to "string: message text"
    )
    override val capability = "sms"
    override val requiredPermissions = listOf(Manifest.permission.SEND_SMS)

    override fun isAvailable(ctx: Context): Boolean =
        ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (!ctx.granted(Manifest.permission.SEND_SMS)) {
            return ActionResult.missingPermission(Manifest.permission.SEND_SMS)
        }
        val number = params.str("number") ?: return ActionResult.error("number is required")
        val body = params.str("body") ?: return ActionResult.error("body is required")
        if (!PhoneNumbers.isPlausible(number)) {
            return ActionResult.error("'$number' does not look like a phone number")
        }
        if (PhoneNumbers.isEmergency(number)) {
            return ActionResult.error("Jarvis will not message emergency services; do it by hand")
        }
        if (body.length > 2000) return ActionResult.error("message body is too long (max 2000 chars)")

        val sms = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                ctx.getSystemService(SmsManager::class.java)
            } else {
                @Suppress("DEPRECATION")
                SmsManager.getDefault()
            }
        } catch (e: Exception) {
            null
        } ?: return ActionResult.error("no SMS service on this device")

        return try {
            val parts = sms.divideMessage(body)
            if (parts.size > 1) {
                sms.sendMultipartTextMessage(number, null, parts, null, null)
            } else {
                sms.sendTextMessage(number, null, body, null, null)
            }
            ActionResult.ok(json("number" to number, "parts" to parts.size, "chars" to body.length))
        } catch (e: SecurityException) {
            ActionResult.missingPermission(Manifest.permission.SEND_SMS)
        } catch (e: IllegalArgumentException) {
            ActionResult.error("SMS rejected: ${e.message ?: "bad number or empty body"}")
        }
    }
}

/** Tier 3 — starts a real call to a real person. */
object PlaceCall : JarvisAction {
    override val id = "place_call"
    override val tier = ActionTier.CONFIRM
    override val description = "Place a phone call immediately."
    override val paramsSchema = mapOf("number" to "string: phone number to call")
    override val capability = "telephony"
    override val requiredPermissions = listOf(Manifest.permission.CALL_PHONE)

    override fun isAvailable(ctx: Context): Boolean =
        ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (!ctx.granted(Manifest.permission.CALL_PHONE)) {
            return ActionResult.missingPermission(Manifest.permission.CALL_PHONE)
        }
        val number = params.str("number") ?: return ActionResult.error("number is required")
        if (!PhoneNumbers.isPlausible(number)) {
            return ActionResult.error("'$number' does not look like a phone number")
        }
        // ACTION_CALL cannot dial emergency numbers anyway; say so honestly
        // rather than failing with a SecurityException.
        if (PhoneNumbers.isEmergency(number)) {
            return ActionResult.error(
                "Android does not allow apps to dial emergency numbers; use the dialer by hand"
            )
        }
        val intent = Intent(Intent.ACTION_CALL, Uri.parse("tel:${Uri.encode(number)}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            ctx.startActivity(intent)
            ActionResult.ok(json("number" to number))
        } catch (e: SecurityException) {
            ActionResult.missingPermission(Manifest.permission.CALL_PHONE)
        } catch (e: ActivityNotFoundException) {
            ActionResult.error("no dialer app on this device")
        }
    }
}

/**
 * Tier 2 — read-only, but it is other people's data, so it asks once.
 * Results are marked untrusted: a contact name is attacker-controllable text.
 */
object ReadContacts : JarvisAction {
    override val id = "read_contacts"
    override val tier = ActionTier.NOTIFY
    override val description = "Look up contacts by name or number."

    /** A contact name is whatever the person who synced it typed. */
    override val untrustedOutput = true
    override val paramsSchema = mapOf(
        "query" to "string: name or number fragment to search for",
        "limit" to "int: maximum matches (default 10)"
    )
    override val capability = "contacts"
    override val requiredPermissions = listOf(Manifest.permission.READ_CONTACTS)

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            if (!ctx.granted(Manifest.permission.READ_CONTACTS)) {
                return@withContext ActionResult.missingPermission(Manifest.permission.READ_CONTACTS)
            }
            val query = params.str("query")
            val limit = params.optInt("limit", 10).coerceIn(1, 50)
            val projection = arrayOf(
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                ContactsContract.CommonDataKinds.Phone.NUMBER,
                ContactsContract.CommonDataKinds.Phone.TYPE
            )
            val uri = if (query != null) {
                Uri.withAppendedPath(
                    ContactsContract.CommonDataKinds.Phone.CONTENT_FILTER_URI,
                    Uri.encode(query)
                )
            } else {
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI
            }
            val results = JSONArray()
            try {
                ctx.contentResolver.query(
                    uri,
                    projection,
                    null,
                    null,
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " ASC"
                )?.use { cursor ->
                    val nameCol = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME)
                    val numberCol = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
                    while (cursor.moveToNext() && results.length() < limit) {
                        results.put(
                            json(
                                "name" to (if (nameCol >= 0) cursor.getString(nameCol) else null),
                                "number" to (if (numberCol >= 0) cursor.getString(numberCol) else null)
                            )
                        )
                    }
                }
            } catch (e: SecurityException) {
                return@withContext ActionResult.missingPermission(Manifest.permission.READ_CONTACTS)
            } catch (e: Exception) {
                return@withContext ActionResult.error("contacts lookup failed: ${e.message ?: "unknown"}")
            }
            ActionResult.ok(
                json("contacts" to results, "count" to results.length()).markUntrusted()
            )
        }
}

/** Tier 1 — a local notification on this phone only. Nothing leaves the device. */
object SendNotification : JarvisAction {
    override val id = "send_notification"
    override val tier = ActionTier.AUTO
    override val description = "Post a local notification on this phone."
    override val paramsSchema = mapOf(
        "title" to "string: notification title",
        "text" to "string: notification body",
        "priority" to "string (optional): low | default | high"
    )
    override val capability = "notifications"
    override val requiredPermissions = listOf(Manifest.permission.POST_NOTIFICATIONS)

    private const val CHANNEL_ID = "jarvis_actions"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            !ctx.granted(Manifest.permission.POST_NOTIFICATIONS)
        ) {
            return ActionResult.missingPermission(Manifest.permission.POST_NOTIFICATIONS)
        }
        val title = params.str("title") ?: return ActionResult.error("title is required")
        val text = params.str("text").orEmpty()
        val nm = ctx.getSystemService(NotificationManager::class.java)
            ?: return ActionResult.error("no notification service")

        val importance = when (params.str("priority")?.lowercase()) {
            "low" -> NotificationManager.IMPORTANCE_LOW
            "high" -> NotificationManager.IMPORTANCE_HIGH
            else -> NotificationManager.IMPORTANCE_DEFAULT
        }
        runCatching {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Jarvis", importance).apply {
                    description = "Notifications Jarvis was asked to post"
                }
            )
        }

        return try {
            val notification = Notification.Builder(ctx, CHANNEL_ID)
                // A framework drawable, so this module needs no app resources.
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .build()
            val notificationId = (System.currentTimeMillis() % Int.MAX_VALUE).toInt()
            nm.notify(notificationId, notification)
            ActionResult.ok(json("notification_id" to notificationId, "title" to title))
        } catch (e: Exception) {
            ActionResult.error("could not post the notification: ${e.message ?: e.javaClass.simpleName}")
        }
    }
}
