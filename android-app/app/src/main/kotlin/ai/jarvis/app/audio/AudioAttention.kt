package ai.jarvis.app.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import android.util.Log
import java.util.concurrent.Executor

private const val TAG = "JarvisAudioAttention"

/**
 * Who has the audio, and whether Jarvis may take it.
 *
 * Two small Android shims that were both missing outright. Before this file
 * there was **no `requestAudioFocus`, no `AudioFocusRequest` and no call-state
 * awareness anywhere in the app**, with two consequences that were reported as
 * one symptom — "it stops working after a phone call":
 *
 *  * A conversation talked over whatever the user was already playing, and did
 *    not stop when a call arrived. It held no focus, so it was never told.
 *  * The always-on listener discovered a call only by *failing*: `AudioRecord`
 *    could not be opened, `onMicUnavailable` fired, and recovery was blind
 *    exponential backoff plus a fifteen-minute inexact alarm. Hanging up is an
 *    edge nothing was watching, so the phone went on not listening for as long
 *    as the backoff and the alarm said, which could be a quarter of an hour
 *    after the call ended.
 *
 * The fix is one of each: [TurnFocus] for the half of the problem where Jarvis
 * is the one making noise, and [CallGuard] for the half where somebody else is.
 */

/**
 * The audio focus a *turn* holds, and gives back.
 *
 * Deliberately scoped to a turn and never to the always-on listener. Focus is a
 * claim on the user's speakers — taking it pauses their music — and a wake-word
 * listener that held focus for the hours it is idle would be an assistant that
 * silenced the phone in order to wait. Detection asks for nothing; a
 * conversation asks for `GAIN_TRANSIENT_EXCLUSIVE`, which is the documented
 * request for speech recognition and which the platform restores from
 * afterwards.
 *
 * `AudioFocusRequest` unconditionally: `minSdk` is 29 and the four-argument
 * `requestAudioFocus` was deprecated at 26, so there is no legacy branch to
 * keep and nothing to suppress.
 *
 * @param onLoss called on the main-thread-ish focus thread when the focus is
 *   gone for good ([AudioManager.AUDIOFOCUS_LOSS]) or for now
 *   ([AudioManager.AUDIOFOCUS_LOSS_TRANSIENT]) — an incoming call is the second.
 *   Both mean the same thing to a conversation: stop. A turn cannot be resumed
 *   halfway through a sentence, so `CAN_DUCK` is treated as a loss too rather
 *   than pretending a quieter assistant is a usable one.
 */
class TurnFocus(
    context: Context,
    private val onLoss: () -> Unit,
) {

    private val audio = context.applicationContext.getSystemService(AudioManager::class.java)

    private val listener = AudioManager.OnAudioFocusChangeListener { change ->
        when (change) {
            AudioManager.AUDIOFOCUS_LOSS,
            AudioManager.AUDIOFOCUS_LOSS_TRANSIENT,
            AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK,
            -> {
                Log.i(TAG, "audio focus lost ($change); ending the turn")
                held = false
                try {
                    onLoss()
                } catch (t: Throwable) {
                    Log.w(TAG, "the focus-loss handler threw", t)
                }
            }
        }
    }

    private val request: AudioFocusRequest =
        AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    // The honest pair for an assistant that is about to speak.
                    // `USAGE_ASSISTANT` is what tells the platform this is a
                    // voice assistant rather than media, which is what makes a
                    // car head unit and a headset route it as speech.
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            // False: a turn interrupted by a call is over. Delayed focus would
            // hand the microphone back minutes later, to a conversation nobody
            // is having any more.
            .setAcceptsDelayedFocusGain(false)
            .setWillPauseWhenDucked(true)
            .setOnAudioFocusChangeListener(listener)
            .build()

    /** True while this object believes it holds focus. */
    @Volatile
    var held = false
        private set

    /**
     * Ask for the audio.
     *
     * @return false when the platform refused — which happens, and is not a
     *   reason to abandon the turn: a refusal means somebody else has an
     *   exclusive claim (a call), and the caller's own microphone attempt is
     *   about to fail with a message the user can act on. Reported so the caller
     *   may decide, never enforced here.
     */
    fun take(): Boolean {
        val manager = audio ?: return false
        val granted = try {
            manager.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        } catch (t: Throwable) {
            Log.w(TAG, "could not request audio focus", t)
            false
        }
        held = granted
        return granted
    }

    /** Give it back. Safe to call when it was never taken, and more than once. */
    fun release() {
        val manager = audio ?: return
        if (!held) return
        held = false
        try {
            manager.abandonAudioFocusRequest(request)
        } catch (t: Throwable) {
            Log.w(TAG, "could not abandon audio focus", t)
        }
    }
}

/**
 * Whether a call is in progress, and the edge when that changes.
 *
 * ## Why the audio mode and not TelephonyManager
 *
 * `TelephonyManager` is the obvious answer and it is the wrong one here, for
 * three reasons that all point the same way:
 *
 *  * **It needs a dangerous permission.** `TelephonyCallback.CallStateListener`
 *    requires `READ_PHONE_STATE` from API 31. Spending a permission the user has
 *    to grant — and that `runtime_permissions_test.py` would then require the
 *    app to request — on "should the microphone pause" is a bad trade.
 *  * **It cannot see the calls people actually make.** A WhatsApp, Signal or
 *    Meet call is not telephony. It holds the microphone exactly as hard.
 *  * **The audio mode sees both**, needs nothing, and is the same probe
 *    [ai.jarvis.app.channel.PresenceReporter] already uses to decide whether
 *    this phone can make a noise the user would hear.
 *
 * ## The honest limit
 *
 * `AudioManager.addOnModeChangedListener` arrived in API 31. Below that there is
 * no callback for this and the platform offers no substitute, so [edgeDriven] is
 * false and a caller gets [inCall] on demand rather than a push. The wake
 * listener's existing retry is what covers those devices; it is slower, and this
 * says so rather than implying every phone gets the fast path.
 */
class CallGuard(
    context: Context,
    /** Called when a call starts (true) or ends (false). Never called twice the same way. */
    private val onCallChanged: (Boolean) -> Unit,
) {

    private val audio = context.applicationContext.getSystemService(AudioManager::class.java)

    /** True when this device can report the change rather than being asked. */
    val edgeDriven: Boolean get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S

    /**
     * Somebody is on a call right now.
     *
     * `MODE_RINGTONE` counts: the microphone is about to go, and a wake listener
     * that keeps the recorder open until the user answers is one that loses the
     * race for it — and takes the first two seconds of the call's audio path
     * with it.
     */
    val inCall: Boolean
        get() = when (mode()) {
            AudioManager.MODE_IN_CALL,
            AudioManager.MODE_IN_COMMUNICATION,
            AudioManager.MODE_RINGTONE,
            -> true

            else -> false
        }

    @Volatile
    private var last = false
    private var registered = false

    private val executor = Executor { it.run() }

    private val modeListener = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        AudioManager.OnModeChangedListener { publish() }
    } else {
        null
    }

    /** Begin watching, and record the current state without announcing it. */
    fun start() {
        last = inCall
        if (registered) return
        val manager = audio ?: return
        val listener = modeListener ?: return
        try {
            manager.addOnModeChangedListener(executor, listener)
            registered = true
        } catch (t: Throwable) {
            Log.w(TAG, "could not watch the audio mode", t)
        }
    }

    fun stop() {
        if (!registered) return
        registered = false
        val manager = audio ?: return
        val listener = modeListener ?: return
        try {
            manager.removeOnModeChangedListener(listener)
        } catch (t: Throwable) {
            Log.d(TAG, "could not stop watching the audio mode", t)
        }
    }

    /**
     * Re-read the mode and report a change. Public because a device below API 31
     * has no callback, so its caller drives this from whatever edges it does
     * have — and because re-reading after a failed `AudioRecord` open is how the
     * listener tells "a call took the microphone" from "something else did".
     */
    fun publish() {
        val now = inCall
        if (now == last) return
        last = now
        Log.i(TAG, if (now) "a call started" else "the call ended")
        try {
            onCallChanged(now)
        } catch (t: Throwable) {
            Log.w(TAG, "the call-state handler threw", t)
        }
    }

    private fun mode(): Int = try {
        audio?.mode ?: AudioManager.MODE_NORMAL
    } catch (t: Throwable) {
        Log.d(TAG, "could not read the audio mode", t)
        AudioManager.MODE_NORMAL
    }
}
