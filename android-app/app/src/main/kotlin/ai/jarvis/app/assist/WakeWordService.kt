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
        try {
            startActivity(intent)
        } catch (t: Throwable) {
            // Android 10+ refuses background activity starts unless the app has
            // an exemption. The notification is the fallback: a full-screen
            // intent the user can tap, rather than a wake word that silently
            // does nothing.
            Log.w(TAG, "could not start the assist popup from the background", t)
            showHeard()
            resume()
        }
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

    private fun showHeard() = updateNotification("Heard you — tap to talk")

    private fun showProblem(reason: String) = updateNotification(reason)

    companion object {
        private const val TAG = "JarvisWake"
        private const val CHANNEL = "jarvis-wake"
        private const val NOTIFICATION_ID = 0x4A57 // 'JW'

        const val ACTION_STOP = "ai.jarvis.app.WAKE_STOP"
        const val ACTION_PAUSE = "ai.jarvis.app.WAKE_PAUSE"
        const val ACTION_RESUME = "ai.jarvis.app.WAKE_RESUME"

        private const val WAITING = "Say “Hey Jarvis” at any time"
        private const val WAITING_PAUSED = "Paused while you are talking"

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
