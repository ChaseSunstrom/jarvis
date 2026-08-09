package ai.jarvis.app.companion

import ai.jarvis.app.JarvisApp
import ai.jarvis.app.R
import ai.jarvis.app.ui.JarvisUi
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/**
 * Jarvis-style notifications for proactive messages: reactor icon, accent
 * colour, tappable straight through to the orb.
 *
 * Two channels, because they are two different interruptions and the user
 * deserves two switches:
 *
 *  * [CHANNEL_QUESTION] (high) — a question waiting for an answer, and a
 *    spoken message that arrived while the app was in the background. Both need
 *    to be noticed now.
 *  * [JarvisApp.CHANNEL_ALERTS] (default) — a quiet `notify`. Reused rather
 *    than duplicated: it already exists for "anything Jarvis posts on the
 *    user's behalf", which is exactly what this is.
 *
 * The Tier-3 approval channel is deliberately NOT reused. It bypasses Do Not
 * Disturb and is described to the user as consent requests; a companion
 * question borrowing that would both muddy the switch and quietly inherit a DND
 * exemption nobody asked for.
 *
 * **Lock screen.** Every notification is `VISIBILITY_PRIVATE` with a redacted
 * public version, so a sensitive question reads as
 * [CompanionAskGate.HIDDEN_TEXT] on the keyguard and spells itself out only
 * once the phone is unlocked. That is the notification half of the same rule
 * [CompanionAskGate] enforces inside the activity.
 */
object CompanionNotifications {

    /** Questions and background speech. Created lazily, on first use. */
    const val CHANNEL_QUESTION = "jarvis_companion"

    private const val TAG = "JarvisCompanionNotif"

    private val codes = AtomicInteger(7000)
    private val posted = ConcurrentHashMap<String, Int>()

    @Volatile
    private var channelReady = false

    /**
     * Post the notification for [message]. Returns false when it could not be
     * shown at all — which the caller must report as `undeliverable` rather
     * than swallow, so the server tries another device.
     */
    fun post(context: Context, message: CompanionProtocol.Message, intent: Intent): Boolean {
        val app = context.applicationContext
        val nm = app.getSystemService(NotificationManager::class.java) ?: return false
        ensureChannel(app, nm)

        val code = posted[message.messageId] ?: codes.incrementAndGet()
        val pending = PendingIntent.getActivity(
            app,
            code,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val high = message.wantsAnswer || message.mode == CompanionProtocol.MODE_SPEAK ||
            message.importance == "critical"
        val channel = if (high) CHANNEL_QUESTION else JarvisApp.CHANNEL_ALERTS

        val title = titleFor(message)
        val body = message.text.trim().ifEmpty { CompanionAskGate.NO_TEXT }

        val builder = Notification.Builder(app, channel)
            .setSmallIcon(R.drawable.ic_jarvis_status)
            .setColor(JarvisUi.ACCENT)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setCategory(
                if (message.wantsAnswer) Notification.CATEGORY_STATUS
                else Notification.CATEGORY_MESSAGE
            )
            .setContentIntent(pending)
            .setAutoCancel(!message.wantsAnswer)
            // The body may quote a web page the model just read. Keep it off the
            // keyguard and let the redacted public version speak for it there.
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setPublicVersion(redacted(app, channel, title, pending))
            .setShowWhen(true)

        if (message.wantsAnswer) {
            // `timeout_s` is how long the SERVER waits for an answer, so once
            // it has passed the question is dead and the notification should go
            // with it. It is emphatically NOT a shelf life for a plain message:
            // "the washing machine finished" carries the server's 30s notify
            // timeout, and self-destructing after 30 seconds is how a user
            // misses the message the whole feature exists to deliver.
            builder.setTimeoutAfter(message.timeoutMs)
            // A question must not be swiped into oblivion without an answer:
            // dismissing it is a deliberate act inside the activity, which is
            // what reports `dismissed` and lets the server escalate.
            builder.setOngoing(true)
            // On Android 14+ full-screen intents are reserved for calling and
            // alarm apps, so this usually degrades to a heads-up notification.
            // That is fine — the question still lands in front of the user.
            builder.setFullScreenIntent(pending, true)
        }

        return try {
            nm.notify(code, builder.build())
            posted[message.messageId] = code
            true
        } catch (t: Throwable) {
            Log.w(TAG, "could not post the companion notification", t)
            false
        }
    }

    /** Take down the notification for [messageId], if there is one. */
    fun cancel(context: Context, messageId: String) {
        val code = posted.remove(messageId) ?: return
        try {
            context.applicationContext
                .getSystemService(NotificationManager::class.java)
                ?.cancel(code)
        } catch (t: Throwable) {
            Log.w(TAG, "could not cancel the companion notification", t)
        }
    }

    /**
     * Take every companion notification down.
     *
     * Called when the channel goes away for good. A question whose ledger entry
     * has just been cleared can no longer be answered — tapping it lands on an
     * id nobody remembers — so leaving it on the shade is offering the user a
     * control that does nothing.
     */
    fun cancelAll(context: Context) {
        val ids = posted.keys.toList()
        for (id in ids) cancel(context, id)
    }

    /** How many companion notifications this process believes are up. */
    val postedCount: Int get() = posted.size

    /** Title text. Sensitive questions do not put themselves in the title. */
    fun titleFor(message: CompanionProtocol.Message): String = when {
        message.wantsAnswer -> "Jarvis has a question"
        message.mode == CompanionProtocol.MODE_SPEAK -> "Jarvis wants to say something"
        else -> "Jarvis"
    }

    private fun redacted(
        app: Context,
        channel: String,
        title: String,
        pending: PendingIntent,
    ): Notification = Notification.Builder(app, channel)
        .setSmallIcon(R.drawable.ic_jarvis_status)
        .setColor(JarvisUi.ACCENT)
        .setContentTitle(title)
        .setContentText(CompanionAskGate.HIDDEN_TEXT)
        .setContentIntent(pending)
        .build()

    private fun ensureChannel(app: Context, nm: NotificationManager) {
        if (channelReady) return
        try {
            val channel = NotificationChannel(
                CHANNEL_QUESTION,
                "Jarvis questions",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description =
                    "Questions Jarvis needs an answer to, and messages it tried to " +
                        "speak while the app was closed."
                enableVibration(true)
                setShowBadge(true)
                lockscreenVisibility = Notification.VISIBILITY_PRIVATE
            }
            nm.createNotificationChannel(channel)
            channelReady = true
        } catch (t: Throwable) {
            // A missing channel means notify() will fail, which the caller
            // already reports as undeliverable. Never take the process down for
            // it.
            Log.w(TAG, "could not create the companion notification channel", t)
        }
    }
}
