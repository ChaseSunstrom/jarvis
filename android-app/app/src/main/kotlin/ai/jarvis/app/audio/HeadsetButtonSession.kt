package ai.jarvis.app.audio

import ai.jarvis.app.JarvisAssistActivity
import ai.jarvis.app.assist.JarvisConversation
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.ApprovalBridge
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.SystemClock
import android.util.Log
import android.view.KeyEvent

/**
 * The thing that presses the headset button.
 *
 * [MediaButtonGate] is pure logic, unit-tested on a JVM and mirrored across all
 * 400 input combinations by `tools/media_button_test.py`. It had no caller.
 * There was no `MediaSession` anywhere in the app, so no media button event
 * ever reached this process, so the gate's rules — including the security rule
 * that a press may never answer a consent prompt — described a feature that did
 * not exist. `docs/earpiece.md` documented it as shipped.
 *
 * This is the missing half: an actual `MediaSession`, active only while the
 * user has opted in, whose callback asks the gate what to do and does it.
 *
 * ## What the platform will and will not deliver
 *
 * A media button goes to **one** session — the one the framework considers the
 * current media button receiver, which is broadly the most recently active
 * session. That has a consequence worth being straight about: while another app
 * is genuinely playing, that app usually owns the button and Jarvis never sees
 * the press at all. [MediaButtonGate.Action.PASS_TO_MEDIA] is therefore mostly
 * belt-and-braces — the case it really covers is a paused player that still
 * holds the session, where a tap means "resume" and Jarvis must not take it.
 *
 * The session deliberately publishes **no metadata and no playback state other
 * than STOPPED**. Jarvis is not a media app; a session announcing itself as one
 * would put a phantom player in the shade and on the lock screen.
 *
 * ## Thread
 *
 * `MediaSession.Callback` runs on the thread of the handler the session was
 * built with — here, the caller's looper, which is the service's main thread.
 * Nothing in [onMediaButtonEvent] blocks.
 */
class HeadsetButtonSession(context: Context) {

    private val appContext = context.applicationContext
    private val config = JarvisConfig(appContext)

    private var session: MediaSession? = null

    /** Debounce clock. See [MediaButtonGate.DEBOUNCE_MS]. */
    private var lastAcceptedAt = 0L

    /**
     * Bring the session into line with the user's settings.
     *
     * Idempotent, and safe to call on every config change and every service
     * restart: with the setting off it tears the session down rather than
     * leaving an inert one registered, because an inactive-but-present session
     * still competes for the button.
     */
    fun refresh() {
        if (config.headsetMode && config.headsetButton) start() else stop()
    }

    private fun start() {
        if (session != null) return
        session = try {
            MediaSession(appContext, TAG).apply {
                setCallback(callback)
                // STOPPED with no actions: enough to be a session that can
                // receive a button, not enough to look like a player.
                setPlaybackState(
                    PlaybackState.Builder()
                        .setState(PlaybackState.STATE_STOPPED, 0L, 0f)
                        .setActions(0L)
                        .build()
                )
                isActive = true
            }
        } catch (t: Throwable) {
            // A ROM without a media session service, or one that refuses a
            // session to a background app. The button simply keeps doing what
            // it did before Jarvis existed.
            Log.w(TAG, "could not take the headset button", t)
            null
        }
    }

    fun stop() {
        val current = session ?: return
        session = null
        runCatching {
            current.isActive = false
            current.release()
        }.onFailure { Log.w(TAG, "releasing the media session failed", it) }
    }

    private val callback = object : MediaSession.Callback() {
        override fun onMediaButtonEvent(intent: Intent): Boolean {
            val event = keyEventOf(intent) ?: return false
            if (!INTERESTING.contains(event.keyCode)) return false
            // One decision per press, on the release, because that is the only
            // moment the hold duration is known — and a long press is the whole
            // difference between summoning Jarvis and pausing a podcast.
            if (event.action != KeyEvent.ACTION_UP) return true
            if (event.repeatCount > 0) return true

            val heldMs = (event.eventTime - event.downTime).coerceAtLeast(0L)
            val now = SystemClock.elapsedRealtime()
            val sinceLast =
                if (lastAcceptedAt == 0L) Long.MAX_VALUE else now - lastAcceptedAt

            val action = MediaButtonGate.decide(
                headsetModeEnabled = config.headsetMode && config.headsetButton,
                consentPending = ApprovalBridge.anyPending,
                inConversation = JarvisConversation.live != null,
                musicActive = musicActive(),
                heldMs = heldMs,
                msSinceLastAccepted = sinceLast,
            )
            if (MediaButtonGate.resetsDebounce(action)) lastAcceptedAt = now

            return when (action) {
                // Consumed and dropped. Returning true is the point: false
                // would hand the press onward, and rule 1 says a press during a
                // consent prompt reaches nothing at all.
                MediaButtonGate.Action.IGNORE -> true

                // Not ours. false lets the framework route it as it would have.
                MediaButtonGate.Action.PASS_TO_MEDIA -> false

                MediaButtonGate.Action.START_TURN -> { startTurn(); true }

                MediaButtonGate.Action.END_TURN -> {
                    // A conversation that ended between the decision and here
                    // is not an error; there is simply nothing to end.
                    JarvisConversation.live?.endTurnFromButton()
                    true
                }
            }
        }
    }

    private fun startTurn() {
        // The same surface the wake word opens, and for the same reasons: it is
        // showWhenLocked and turnScreenOn, so the button works with the phone
        // in a pocket. What can be APPROVED from there is a separate question,
        // and ConsentGate answers it.
        val intent = Intent(appContext, JarvisAssistActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            .putExtra(JarvisAssistActivity.EXTRA_FROM_HEADSET_BUTTON, true)
        try {
            appContext.startActivity(intent)
        } catch (t: Throwable) {
            Log.w(TAG, "could not open the assist surface from the headset button", t)
        }
    }

    private fun musicActive(): Boolean = try {
        appContext.getSystemService(AudioManager::class.java)?.isMusicActive == true
    } catch (t: Throwable) {
        Log.w(TAG, "music-active check failed", t)
        false
    }

    private fun keyEventOf(intent: Intent): KeyEvent? = try {
        if (intent.action != Intent.ACTION_MEDIA_BUTTON) {
            null
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra<KeyEvent>(Intent.EXTRA_KEY_EVENT)
        }
    } catch (t: Throwable) {
        Log.w(TAG, "unreadable media button intent", t)
        null
    }

    private companion object {
        const val TAG = "JarvisHeadsetButton"

        /**
         * The keys a one-button headset actually sends. HEADSETHOOK is the
         * wired one; PLAY_PAUSE is what Bluetooth AVRCP sends for the same
         * physical press. PLAY and PAUSE arrive from headsets with separate
         * transport keys, where a press already means something specific and
         * Jarvis has no business intercepting it.
         */
        val INTERESTING = setOf(
            KeyEvent.KEYCODE_HEADSETHOOK,
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
        )
    }
}
