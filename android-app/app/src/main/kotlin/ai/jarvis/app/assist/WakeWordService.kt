package ai.jarvis.app.assist

import ai.jarvis.app.JarvisAssistActivity
import ai.jarvis.app.R
import ai.jarvis.app.config.JarvisConfig
import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import androidx.core.app.ServiceCompat

/**
 * Always-on "Hey Jarvis", for when the phone is not in your hand.
 *
 * `JarvisConfig.wakeWordEnabled` and its gate have existed since the app was
 * written and nothing ever read them: every voice path needed a button first,
 * so the switch was a switch for nothing. This is the listener.
 *
 * **Detection happens on the server, not here.** The mic streams continuously
 * into an `assist_pipeline/run` that starts at the `wake_word` stage, and
 * openWakeWord in jarvis-core decides when its name was said. That is a
 * deliberate trade: it costs a constant upstream trickle of audio to a machine
 * the user owns, and it avoids shipping a detector — and a second copy of the
 * audio path — onto the phone. Nothing is written to disk at either end.
 *
 * **Why a foreground service.** Android has given third-party apps no low-power
 * hotword path since the DSP APIs were closed off, so an open mic is the only
 * implementation available, and an open mic must be visible. The notification
 * is not decoration or a platform tax to be minimised: it is the only thing
 * telling the person holding the phone that something is listening, and it
 * says so plainly and offers a STOP.
 *
 * The mic is released the moment a conversation starts and re-acquired when it
 * ends, because two `AudioRecord`s on one device is a coin toss over which one
 * gets the audio — see [pause].
 */
class WakeWordService : Service(), AssistPipelineClient.Callbacks {

    private val main = Handler(Looper.getMainLooper())
    private lateinit var config: JarvisConfig

    private var client: AssistPipelineClient? = null
    private var mic: MicStreamer? = null
    private var running = false

    /** Consecutive failed connects, for the backoff. */
    private var failures = 0

    private val reconnect = Runnable { if (running) openLink() }

    /**
     * Take the microphone back when a wake word led nowhere.
     *
     * Cancelled by ACTION_PAUSE, which the assist activity sends as it starts.
     */
    private val rearm = Runnable {
        if (!running || !config.wakeWordEnabled) return@Runnable
        Log.i(TAG, "no conversation took the mic; listening again")
        resume()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        config = JarvisConfig(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                // An explicit stop is a decision, not a pause: turn the setting
                // off too, or the next thing that calls ensureRunning quietly
                // starts listening again and the STOP button looks broken.
                config.wakeWordEnabled = false
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_PAUSE -> {
                // A conversation took the mic, so the re-arm safety net is not needed.
                main.removeCallbacks(rearm)
                pause()
                return START_STICKY
            }
            ACTION_RESUME -> {
                resume()
                return START_STICKY
            }
        }

        if (!config.wakeWordEnabled || !hasMic()) {
            Log.i(TAG, "not listening: enabled=${config.wakeWordEnabled} mic=${hasMic()}")
            stopSelf()
            return START_NOT_STICKY
        }

        enterForeground()
        if (!running) {
            running = true
            openLink()
        }
        // STICKY: a wake-word listener the system killed under memory pressure
        // should come back, and onStartCommand re-checks every precondition
        // rather than assuming the previous state survived.
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        main.removeCallbacks(reconnect)
        main.removeCallbacks(rearm)
        closeLink()
        super.onDestroy()
    }

    // --- the link -----------------------------------------------------------

    private fun openLink() {
        if (!running) return
        closeLink()

        val url = config.serverUrl
        if (url.isEmpty() || config.token.isEmpty()) {
            Log.w(TAG, "no server configured; stopping")
            stopSelf()
            return
        }

        client = AssistPipelineClient(
            url,
            config.token,
            this,
            AssistPipelineClient.StartStage.WAKE_WORD,
            serverKind = config.serverKind,
            onKindResolved = { config.serverKind = it },
        ).also { it.connect(config.pipeline) }

        mic = MicStreamer(
            onPcm = { buf, len -> client?.sendAudio(buf, len) },
            onLevel = { /* nothing to draw: there is no surface while waiting */ },
            onUnavailable = { reason ->
                // A dead mic while waiting is silent by nature — there is no
                // screen to put it on — so it goes in the notification, which
                // is the one surface this service always has.
                Log.w(TAG, "capture unavailable: $reason")
                showProblem(reason)
                stopSelf()
            },
        ).also { it.start() }
    }

    private fun closeLink() {
        mic?.stop(); mic = null
        client?.close(); client = null
    }

    /**
     * Give up the microphone.
     *
     * Called when a conversation is about to start. Two `AudioRecord`s open on
     * one device is a coin toss over which gets the audio, and losing that toss
     * means the conversation the user just triggered hears nothing — the exact
     * symptom this whole area has been plagued by.
     */
    private fun pause() {
        main.removeCallbacks(reconnect)
        closeLink()
        updateNotification(WAITING_PAUSED)
    }

    private fun resume() {
        if (!running || !config.wakeWordEnabled) return
        updateNotification(WAITING)
        openLink()
    }

    private fun scheduleReconnect() {
        if (!running) return
        // Exponential, capped. A server that is off overnight must not become a
        // radio that retries every second until the battery is gone.
        val delay = (BACKOFF_BASE_MS shl failures.coerceAtMost(5)).coerceAtMost(BACKOFF_MAX_MS)
        failures++
        main.removeCallbacks(reconnect)
        main.postDelayed(reconnect, delay)
    }

    private fun hasMic(): Boolean =
        checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    // --- AssistPipelineClient.Callbacks --------------------------------------

    override fun onWakeWord(name: String) {
        Log.i(TAG, "wake word heard")
        failures = 0
        // Hand the conversation to the popup and get out of the way. The
        // activity opens its own pipeline run; this service keeps the mic only
        // until that happens.
        pause()
        val intent = Intent(this, JarvisAssistActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            putExtra(JarvisAssistActivity.EXTRA_FROM_WAKE_WORD, true)
        }
        // Both, in this order, on purpose.
        //
        // A direct start is what puts the popup up instantly, and it works when
        // the app is already foreground or the user has granted "display over
        // other apps". Android 10+ silently refuses it otherwise — and silently
        // is the problem: the call does not throw, the activity simply never
        // appears, so a `try/catch` around it is not the safety net it looks
        // like. The full-screen intent is the mechanism the platform actually
        // provides for this (it is how an incoming call gets on screen), so it
        // is posted every time rather than only in a catch block that may never
        // run. If the direct start worked, the notification is redundant and
        // the user never sees it; if it did not, this is what they get.
        try {
            startActivity(intent)
        } catch (t: Throwable) {
            Log.w(TAG, "could not start the assist popup directly", t)
        }
        showHeard(intent)

        // The mic was handed over above. If nothing takes it — a heads-up the
        // user ignored, a start the system dropped — the listener would sit
        // paused forever and the wake word would appear to stop working until
        // the phone was rebooted. The activity cancels this by sending
        // ACTION_PAUSE as it starts.
        main.removeCallbacks(rearm)
        main.postDelayed(rearm, HANDOFF_GRACE_MS)
    }

    override fun onState(state: AssistPipelineClient.State) {
        if (state == AssistPipelineClient.State.LISTENING) failures = 0
    }

    override fun onError(message: String) {
        Log.w(TAG, "link error: $message")
        closeLink()
        scheduleReconnect()
    }

    override fun onRunEnd() {
        // A wake run that ended without a detection (the server closed the run,
        // or it timed out) just needs another one.
        if (running) openLink()
    }

    override fun onTranscript(text: String) = Unit
    override fun onResponseDelta(delta: String) = Unit
    override fun onResponseFinal(text: String) = Unit
    override fun onTtsUrl(absoluteUrl: String) = Unit

    // --- the notification ----------------------------------------------------

    private fun enterForeground() {
        ensureChannel()
        try {
            ServiceCompat.startForeground(
                this,
                NOTIFICATION_ID,
                buildNotification(WAITING),
                // The honest type, and the one the platform requires before it
                // will let a background service touch the mic at all on 34+.
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
            )
        } catch (t: Throwable) {
            Log.w(TAG, "could not enter the foreground", t)
            stopSelf()
        }
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL) != null) return
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Listening for “Hey Jarvis”", NotificationManager.IMPORTANCE_LOW)
                .apply {
                    description =
                        "Shown whenever Jarvis is holding the microphone open. " +
                            "Dismissing this stops it listening."
                    setShowBadge(false)
                }
        )
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, JarvisAssistActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stop = PendingIntent.getService(
            this,
            1,
            Intent(this, WakeWordService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_jarvis_status)
            .setContentTitle("Jarvis is listening")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            // The Icon overload, spelled out: `null` alone cannot pick
            // between Builder(Icon, …) and the deprecated Builder(Int, …).
            .addAction(Notification.Action.Builder(null as Icon?, "STOP", stop).build())
            .build()
    }

    private fun updateNotification(text: String) {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        manager.notify(NOTIFICATION_ID, buildNotification(text))
    }

    /**
     * Put the conversation on screen from the background.
     *
     * A full-screen intent on a HIGH-importance channel is the platform's own
     * answer to "something is happening now and the user must see it" — the
     * incoming-call mechanism. Locked, it takes over the screen; unlocked, it
     * arrives as a heads-up the user can tap. Either way the wake word leads
     * somewhere, which a background `startActivity` that the system quietly
     * dropped does not.
     */
    private fun showHeard(open: Intent) {
        ensureAlertChannel()
        val full = PendingIntent.getActivity(
            this,
            2,
            open,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val note = Notification.Builder(this, CHANNEL_ALERT)
            .setSmallIcon(R.drawable.ic_jarvis_status)
            .setContentTitle("Jarvis is listening")
            .setContentText("Heard you — tap to talk")
            .setContentIntent(full)
            .setCategory(Notification.CATEGORY_CALL)
            .setAutoCancel(true)
            // `true`: this is the whole point — without it the platform treats
            // the full-screen intent as optional and shows only the heads-up.
            .setFullScreenIntent(full, true)
            .build()
        getSystemService(NotificationManager::class.java)?.notify(ALERT_ID, note)
    }

    private fun ensureAlertChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ALERT) != null) return
        // Separate from the ongoing "listening" channel, and HIGH rather than
        // LOW: a full-screen intent on a low-importance channel is ignored, and
        // making the always-there notification high would buzz on every start.
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ALERT,
                "Jarvis heard you",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Shown for a moment when the wake word starts a conversation."
                setShowBadge(false)
            }
        )
    }

    private fun showProblem(reason: String) = updateNotification(reason)

    companion object {
        private const val TAG = "JarvisWake"
        private const val CHANNEL = "jarvis-wake"
        private const val CHANNEL_ALERT = "jarvis-wake-heard"
        private const val NOTIFICATION_ID = 0x4A57 // 'JW'
        private const val ALERT_ID = 0x4A58 // 'JX'

        const val ACTION_STOP = "ai.jarvis.app.WAKE_STOP"
        const val ACTION_PAUSE = "ai.jarvis.app.WAKE_PAUSE"
        const val ACTION_RESUME = "ai.jarvis.app.WAKE_RESUME"

        private const val WAITING = "Say “Hey Jarvis” at any time"
        private const val WAITING_PAUSED = "Paused while you are talking"

        /**
         * How long a wake word gets to become a conversation before the mic is
         * taken back. Long enough to walk to the phone and tap the heads-up.
         */
        private const val HANDOFF_GRACE_MS = 30_000L

        private const val BACKOFF_BASE_MS = 2_000L
        private const val BACKOFF_MAX_MS = 60_000L

        /**
         * Start listening if the user has asked for it. Safe to call repeatedly
         * and from anywhere — it checks the setting itself rather than trusting
         * the caller to.
         */
        fun ensureRunning(context: Context) {
            val config = JarvisConfig(context)
            if (!config.wakeWordEnabled) return
            val intent = Intent(context, WakeWordService::class.java)
            try {
                context.startForegroundService(intent)
            } catch (t: Throwable) {
                // Android 12+ throws if this is called from the background
                // without an exemption. Not fatal: the next start from a
                // resumed activity succeeds, and BootReceiver retries.
                Log.w(TAG, "could not start the wake listener", t)
            }
        }

        /** Hand the microphone to a conversation. */
        fun pause(context: Context) = send(context, ACTION_PAUSE)

        /** Take it back when the conversation is over. */
        fun resume(context: Context) = send(context, ACTION_RESUME)

        private fun send(context: Context, action: String) {
            if (!JarvisConfig(context).wakeWordEnabled) return
            try {
                context.startService(Intent(context, WakeWordService::class.java).setAction(action))
            } catch (t: Throwable) {
                Log.w(TAG, "could not deliver $action", t)
            }
        }
    }
}
