package ai.jarvis.app.assist

import ai.jarvis.app.JarvisAssistActivity
import ai.jarvis.app.ListenTrampolineActivity
import ai.jarvis.app.R
import ai.jarvis.app.audio.CallGuard
import ai.jarvis.app.companion.CompanionMessageHandler
import ai.jarvis.app.companion.ConversationAskHost
import ai.jarvis.app.compat.GrapheneCompat
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.config.WakeWordGate
import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.PromptPresence
import android.Manifest
import android.app.AlarmManager
import android.app.KeyguardManager
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
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import androidx.core.app.ServiceCompat

/**
 * Always-on "Hey Jarvis", for when the phone is not in your hand.
 *
 * `JarvisConfig.wakeWordEnabled` and its gate have existed since the app was
 * written and nothing ever read them: every voice path needed a button first,
 * so the switch was a switch for nothing. This is the listener.
 *
 * **Where detection happens is now a choice, and the two differ in one
 * important way.**
 *
 *  * *On the server* — the original path and still the default. The mic streams
 *    continuously into an `assist_pipeline/run` starting at the `wake_word`
 *    stage, and openWakeWord in jarvis-core decides. It needs no download and
 *    no model on the phone, and it costs a permanently open socket carrying
 *    everything the microphone hears to a machine down the hall.
 *  * *On this phone* — [OnDeviceWakeWord], when the user has turned it on and
 *    the weights have been fetched from their own server ([ModelStore]). No
 *    socket exists until the name has been said, so nothing is uploaded until
 *    there is something to say.
 *
 * The second is strictly better on privacy, battery and working-while-offline,
 * and it is still opt-in, because it depends on files that may not be there —
 * and a feature that silently depends on a missing file is one that silently
 * stops working. [openLocalListener] returns false for every "cannot", and the
 * server path is what happens then. Nothing is written to disk at either end.
 *
 * **Why a foreground service.** Android has given third-party apps no low-power
 * hotword path since the DSP APIs were closed off, so an open mic is the only
 * implementation available, and an open mic must be visible. The notification
 * is not decoration or a platform tax to be minimised: it is the only thing
 * telling the person holding the phone that something is listening, and it
 * says so plainly and offers a STOP.
 *
 * **Staying up is its own problem, and most of this file.** A microphone-typed
 * foreground service cannot be started from the background — `BOOT_COMPLETED`
 * is not an exemption for the while-in-use types — so after a reboot the
 * listener simply was not running and the only cure was opening the app. Three
 * things fix that, and each is here because the failure it covers is invisible
 * from the phone:
 *
 *  1. [WakeStartPolicy] decides in advance whether a start will be allowed, and
 *     when it will not, [tellTheUser] puts it one tap away instead of logging a
 *     warning nobody can read.
 *  2. A failure to open the mic no longer stops the service. It used to, which
 *     turned every transient conflict — a phone call, another app recording —
 *     into a listener that stayed dead until the app was opened.
 *  3. [armHeartbeat] re-checks every quarter of an hour, so a process the system
 *     killed comes back on its own.
 *
 * The mic is released the moment a conversation starts and re-acquired when it
 * ends, because two `AudioRecord`s on one device is a coin toss over which one
 * gets the audio — see [pause].
 *
 * **Two things now decide whether the microphone is open at all, and neither
 * used to exist here.**
 *
 *  1. [WakeListenWatch] — the battery policy. `WakeWordGate` had implemented it
 *     since the app was written and nothing called it, so four settings and a
 *     whole section of the settings screen were stored and inert. It is
 *     consulted before every open and re-consulted on every edge that can change
 *     its answer.
 *  2. [CallGuard] — a call in progress. A call was previously discovered by
 *     *failing to open the recorder*, and recovery was blind exponential backoff
 *     plus the quarter-hourly alarm, so hanging up left the phone deaf for as
 *     long as those said. Hanging up is an edge, and now something is watching
 *     it.
 */
class WakeWordService : Service(), AssistPipelineClient.Callbacks {

    private val main = Handler(Looper.getMainLooper())
    private lateinit var config: JarvisConfig

    private var client: AssistPipelineClient? = null
    private var mic: MicStreamer? = null
    private var running = false

    /** Consecutive failed connects, for the backoff. */
    private var failures = 0

    /** Consecutive failures to open the microphone, for its own backoff. */
    private var micFailures = 0

    /** Notices a recorder that is open and handing back nothing. */
    private val silence = MicSilenceWatch()

    /** The on-device detector, when the phone is doing its own listening. */
    private var detector: OnDeviceWakeWord? = null

    /** Turns its per-frame scores into one detection per utterance. */
    private val scorer = WakeScore()

    /** Held only while a wake word is turning into a conversation. */
    private var screenLock: PowerManager.WakeLock? = null

    /** The floating orb, when the wake word led to one. */
    private var overlay: AssistOverlay? = null

    /** The conversation the overlay is showing, if any. */
    private var convo: JarvisConversation? = null

    /**
     * Lets Jarvis put a question to the user *in the orb that is already up*.
     *
     * Registered for exactly as long as the overlay conversation lives. Without
     * it, `ask_user` starts CompanionAskActivity over the top with NEW_TASK,
     * which takes the orb down — and takes itself down when answered, so the
     * conversation the user was having simply ends twice.
     */
    private var askHost: ConversationAskHost? = null

    /**
     * Whether this service has parked the conversation behind a prompt.
     *
     * See [PromptPresence]. Two things had to happen and neither did:
     *
     *  * **The conversation had to be held.** `JarvisConversation` runs an
     *    8-second inactivity timer, and somebody reading a consent prompt is
     *    somebody saying nothing — so eight seconds in, `onIdle` fired,
     *    [endOverlayConversation] ran, and the orb vanished out from under the
     *    prompt. That is the reported *"still closing the overlay when I try to
     *    approve"*. `holdForQuestion` exists for exactly this and says so:
     *    "`running` stays true throughout, deliberately ... so an inactivity
     *    timer or an `onIdle` cannot pull the surface out from under the
     *    question." It had two callers, both for QUESTIONS.
     *  * **The orb had to move.** An overlay window is drawn above every
     *    Activity, so it was sitting on the consent prompt's own buttons.
     *
     * The ACTIVITY surface solved the first half through `onStop`, which an
     * overlay window never gets — which is why the earlier fix could not have
     * helped anybody using the wake word.
     */
    private var heldForPrompt = false

    private val promptListener: (Boolean) -> Unit = { up ->
        main.post {
            if (up) {
                if (!heldForPrompt) {
                    heldForPrompt = convo?.holdForQuestion() ?: false
                    // Hidden even when there was no conversation to hold: the
                    // orb can be up with the turn already finished, and it
                    // would still be over the buttons.
                    overlay?.setHiddenForPrompt(true)
                }
            } else if (heldForPrompt || overlay != null) {
                overlay?.setHiddenForPrompt(false)
                if (heldForPrompt) {
                    heldForPrompt = false
                    convo?.resumeAfterQuestion()
                }
            }
        }
    }

    private val reconnect = Runnable { if (running) openLink() }

    /**
     * The battery policy, as a live thing rather than a class nobody called.
     *
     * Created with the service and torn down with it. Its callback fires on the
     * headset, car-Bluetooth, geofence and hour-boundary edges — see
     * [WakeListenWatch] — and each one is a chance to open a microphone that was
     * closed, or close one that should be.
     */
    private var listenWatch: WakeListenWatch? = null

    /** True while the gate is what is holding the microphone shut. */
    private var heldByGate = false

    /** True while a call is what is holding it shut. */
    private var heldByCall = false

    /**
     * Somebody is on a call. See [ai.jarvis.app.audio.CallGuard].
     *
     * The listener gives the microphone up on the way IN — a wake listener that
     * waits to lose the race for the recorder takes the first seconds of the
     * call's audio path with it — and takes it back on the way out, at once,
     * rather than at the end of a backoff that knows nothing about calls.
     */
    private var calls: CallGuard? = null

    /**
     * Take the microphone back when a wake word led nowhere.
     *
     * Cancelled by ACTION_PAUSE, which the assist activity sends as it starts,
     * and never armed at all when the overlay took the conversation — that path
     * hands the mic back itself.
     */
    private val rearm = Runnable {
        if (!running || !config.wakeWordEnabled) return@Runnable
        Log.i(TAG, "no conversation took the mic; listening again")
        resume()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        PromptPresence.addListener(promptListener)
        config = JarvisConfig(this)
        listenWatch = WakeListenWatch(this, config) { decision -> onGateChanged(decision) }
        calls = CallGuard(this) { inCall -> onCallChanged(inCall) }
    }

    /**
     * The listening policy changed its mind.
     *
     * The two directions are not symmetrical, and that asymmetry is the whole
     * point of driving this from edges: coming back ON must reopen the
     * microphone immediately (the car has just connected, the user is talking to
     * a phone in a cradle), while going OFF must not tear down a conversation
     * that is already in progress. A gate that closed mid-sentence because the
     * clock struck 23:00 would be a worse bug than the one this fixes.
     */
    private fun onGateChanged(decision: WakeWordGate.Decision) {
        if (!running) return
        if (decision.listen) {
            if (heldByGate) {
                heldByGate = false
                Log.i(TAG, "the listening policy allows the microphone again")
                resume()
            }
            return
        }
        if (convo != null) {
            // Mid-conversation. It ends on its own and hands the mic back
            // through onIdle, which calls resume(), which asks the gate again.
            Log.i(TAG, "the listening policy says stop, but a conversation is live")
            return
        }
        heldByGate = true
        pause(decision.explain(config.wakingHourStart, config.wakingHourEnd))
    }

    private fun onCallChanged(inCall: Boolean) {
        if (!running) return
        if (inCall) {
            if (heldByCall) return
            heldByCall = true
            pause(WAITING_IN_CALL)
            return
        }
        // Only act on the way back out of a pause WE took. A mode change that
        // was never a call — a media app briefly asking for MODE_RINGTONE — must
        // not become a reason to reopen a microphone the gate is holding shut.
        if (!heldByCall) return
        heldByCall = false
        // NOT a backoff. The call ending is the exact moment the recorder became
        // available again, and waiting out an exponential delay after it — or
        // worse, the fifteen-minute alarm — is the reported "it stops working
        // after a phone call".
        micFailures = 0
        main.removeCallbacks(reconnect)
        Log.i(TAG, "the call ended; taking the microphone back")
        resume()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                // An explicit stop is a decision, not a pause: turn the setting
                // off too, or the next thing that calls ensureRunning quietly
                // starts listening again and the STOP button looks broken.
                config.wakeWordEnabled = false
                cancelHeartbeat(this)
                clearAttention(this)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_PAUSE -> {
                // A conversation took the mic, so the re-arm safety net is not
                // needed. If our own overlay was up, whatever is starting now
                // supersedes it.
                main.removeCallbacks(rearm)
                endOverlayConversation(giveMicBack = false)
                if (!running) {
                    // A pause delivered to a listener that was not up would
                    // otherwise leave a service with no foreground notification
                    // and nothing to do. It is not a reason to start listening.
                    stopSelf()
                    return START_NOT_STICKY
                }
                pause()
                return START_STICKY
            }
            ACTION_RESUME -> {
                if (running) {
                    resume()
                    return START_STICKY
                }
                // Not running: a resume aimed at a listener the system already
                // killed is a start. Falling through to the ordinary path below
                // re-checks every precondition and enters the foreground, which
                // resume() on its own would not — leaving a service holding a
                // microphone with no notification over it.
            }
        }

        if (!config.wakeWordEnabled) {
            Log.i(TAG, "not listening: the setting is off")
            cancelHeartbeat(this)
            stopSelf()
            return START_NOT_STICKY
        }
        if (!hasMic()) {
            // Not a silent stop. Without the permission this service can never
            // do its job, and the user asked for it to — so say so where they
            // will see it rather than leaving the switch on and nothing behind
            // it.
            Log.i(TAG, "not listening: RECORD_AUDIO is not granted")
            tellTheUser(this, WakeStartPolicy.Route.NEEDS_MIC_PERMISSION)
            stopSelf()
            return START_NOT_STICKY
        }

        if (!enterForeground()) return START_NOT_STICKY
        clearAttention(this)
        armHeartbeat(this)
        if (!running) {
            running = true
            // Started BEFORE the first open, so `openLink` has a gate to ask
            // rather than falling back to "listen" on the very first pass —
            // which would open the microphone at 03:00 exactly once per start
            // and look like the policy flapping.
            listenWatch?.start()
            calls?.start()
            openLink()
        }
        // STICKY: a wake-word listener the system killed under memory pressure
        // should come back, and onStartCommand re-checks every precondition
        // rather than assuming the previous state survived. The heartbeat
        // covers the case where the platform refuses the sticky restart because
        // it would be a background start of a microphone service.
        return START_STICKY
    }

    override fun onDestroy() {
        PromptPresence.removeListener(promptListener)
        running = false
        main.removeCallbacks(reconnect)
        main.removeCallbacks(rearm)
        // Both hold platform registrations — an AudioDeviceCallback and, on 31+,
        // an audio-mode listener — and a service that leaks one keeps waking for
        // an event it can no longer act on.
        listenWatch?.stop()
        calls?.stop()
        endOverlayConversation(giveMicBack = false)
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

        // THE BATTERY POLICY, asked before anything opens a microphone.
        //
        // This call is the whole of what `WakeWordGate` was missing: the policy,
        // its four settings and its section of the settings screen existed for
        // the life of the app with no production caller, which is why that
        // section was labelled "saved, not yet in effect". Asked here rather
        // than in `onStartCommand` because every path that reopens the mic —
        // resume, the reconnect backoff, a run ending — comes through this
        // function, and a check in only one of them is a gate with a way round
        // it.
        //
        // The service stays alive and foreground when the answer is no. It has
        // to: the notification is what tells the user why nothing is listening,
        // and [WakeListenWatch] needs a live process to notice the edge that
        // turns it back on.
        val decision = listenWatch?.decide()
        if (decision != null && !decision.listen) {
            heldByGate = true
            Log.i(TAG, "not listening: ${decision.reason}")
            updateNotification(decision.explain(config.wakingHourStart, config.wakingHourEnd))
            return
        }
        heldByGate = false

        // A recorder opened over a live call loses the race for it, and takes
        // the start of the call's own audio with it. Asked here as well as on
        // the edge, because a device below API 31 has no mode callback at all —
        // see CallGuard.edgeDriven — and this is where it gets its answer.
        if (calls?.inCall == true) {
            heldByCall = true
            Log.i(TAG, "not listening: a call is in progress")
            updateNotification(WAITING_IN_CALL)
            return
        }
        heldByCall = false

        if (openLocalListener()) return

        client = AssistPipelineClient(
            url,
            config.token,
            this,
            AssistPipelineClient.StartStage.WAKE_WORD,
            serverKind = config.serverKind,
            onKindResolved = { config.serverKind = it },
        ).also { it.connect(config.pipeline) }

        silence.reset()
        mic = MicStreamer(
            onPcm = { buf, len -> client?.sendAudio(buf, len) },
            onLevel = { level -> watchForSilence(level) },
            onUnavailable = { reason -> onMicUnavailable(reason) },
        ).also { it.start() }
    }

    private fun closeLink() {
        mic?.stop(); mic = null
        client?.close(); client = null
        detector?.close(); detector = null
        scorer.reset()
    }

    /**
     * Listen with nothing but the microphone, if the phone can.
     *
     * This is the whole point of on-device detection: the server path holds a
     * WebSocket open and pushes 32 KB of audio a second into it, permanently,
     * so that a machine down the hall can decide whether its name was said.
     * When the models are present that decision happens here, and the socket —
     * and the upload — does not exist until it has.
     *
     * @return false when it cannot, which is the common case and never an
     *   error: the setting is off, the weights have not been downloaded, or
     *   ONNX Runtime has no build for this ABI. The caller falls through to the
     *   server, which is the path that has always worked.
     */
    private fun openLocalListener(): Boolean {
        if (!config.wakeWordOnDevice) return false
        val local = OnDeviceWakeWord.open(ModelStore.directory(this)) ?: return false
        detector = local
        scorer.reset()
        silence.reset()
        updateNotification(WAITING_LOCAL)
        mic = MicStreamer(
            onPcm = { buf, len -> onLocalAudio(buf, len) },
            onLevel = { level -> watchForSilence(level) },
            onUnavailable = { reason -> onMicUnavailable(reason) },
        ).also { it.start() }
        Log.i(TAG, "listening on this device; no audio is leaving until the wake word")
        return true
    }

    /**
     * One capture buffer, scored locally.
     *
     * Runs on MicStreamer's capture thread, which is deliberate — the ONNX
     * chain is about a millisecond of work per 80 ms of audio, and hopping to
     * the main thread for every buffer would cost more than it saves. The
     * detection itself is posted to the main thread, because everything it
     * leads to touches the UI.
     */
    private fun onLocalAudio(buffer: ByteArray, length: Int) {
        val score = detector?.score(buffer, length) ?: return
        if (!scorer.onScore(SystemClock.uptimeMillis(), score)) return
        Log.i(TAG, "wake word heard on device")
        main.post { if (running) onWakeWord(LOCAL_WAKE_WORD) }
    }

    /**
     * The microphone could not be opened.
     *
     * This used to call `stopSelf()`, which is the single worst thing it could
     * do: the usual cause is another app holding the recorder for a moment — a
     * phone call, a voice note, the assistant on the lock screen — and a
     * momentary conflict permanently killed always-on listening until the user
     * next opened the app. Retry instead, on the same capped backoff the socket
     * uses, and say what is wrong on the notification that is already there.
     */
    private fun onMicUnavailable(reason: String) {
        Log.w(TAG, "capture unavailable: $reason")
        closeLink()
        if (!hasMic()) {
            // A revoked permission is not transient and retrying it forever is
            // just a warm radio. This is the one case that still stops.
            tellTheUser(this, WakeStartPolicy.Route.NEEDS_MIC_PERMISSION)
            stopSelf()
            return
        }
        // ASK WHY, instead of assuming it was random.
        //
        // Failing to open the recorder used to be the only way this service
        // learned a call was happening, and the answer was always the same blind
        // exponential backoff — capped at a minute, on top of a fifteen-minute
        // alarm. A call is the overwhelmingly common cause and it has an ending
        // that can be watched, so this re-reads the audio mode: `publish` fires
        // the call-started edge if that is what it was, and `onCallChanged`
        // takes the microphone back the instant it ends rather than whenever the
        // backoff happens to land.
        calls?.publish()
        if (calls?.inCall == true) {
            heldByCall = true
            updateNotification(WAITING_IN_CALL)
            main.removeCallbacks(reconnect)
            // How long before looking again depends on whether looking is the
            // MECHANISM or a safety net. On API 31+ the mode listener brings us
            // back the moment the call ends and this timer never fires; below
            // it there is no callback at all — see `CallGuard.edgeDriven` — so
            // this poll is the only thing that will ever notice, and it has to
            // be short enough that hanging up does not mean a lost minute.
            main.postDelayed(
                reconnect,
                if (calls?.edgeDriven == true) CALL_RECHECK_MS else CALL_POLL_MS,
            )
            return
        }
        val delay = micBackoff(micFailures)
        micFailures++
        updateNotification("$reason Retrying.")
        main.removeCallbacks(reconnect)
        main.postDelayed(reconnect, delay)
    }

    /**
     * Watch for the recorder that opens, reads happily and returns zeroes.
     *
     * That is what Android does to a while-in-use foreground service started
     * from the background, and what a GrapheneOS per-app *Sensors* toggle does,
     * and what a hardware mute switch does. None of them produce an error, so
     * without this the notification says "Jarvis is listening" while nothing
     * can reach it — which is worse than saying nothing at all.
     *
     * Reported, not acted on: the fix needs an Activity (see
     * [ListenTrampolineActivity]) and taking the listener down would remove the
     * only surface able to offer one.
     */
    private fun watchForSilence(level: Float) {
        if (silence.onLevel(SystemClock.uptimeMillis(), level)) {
            Log.w(TAG, "the microphone has been returning silence")
            updateNotification(MicSilenceWatch.MUTED_MESSAGE, tapToRestart = true)
        }
    }

    /**
     * Give up the microphone.
     *
     * Called when a conversation is about to start. Two `AudioRecord`s open on
     * one device is a coin toss over which gets the audio, and losing that toss
     * means the conversation the user just triggered hears nothing — the exact
     * symptom this whole area has been plagued by.
     *
     * @param text what the notification says while the microphone is shut. It
     *   is a parameter because there are now four different reasons — a
     *   conversation took it, a call took it, the hour, the place — and a
     *   notification that says "Paused while you are talking" at three in the
     *   morning is a listener that looks broken.
     */
    private fun pause(text: String = WAITING_PAUSED) {
        main.removeCallbacks(reconnect)
        closeLink()
        updateNotification(text)
    }

    private fun resume() {
        if (!running || !config.wakeWordEnabled) return
        updateNotification(WAITING)
        // openLink consults the gate and the call guard, so a resume from any
        // source — a finished conversation, the re-arm net, a call ending —
        // cannot walk past either of them.
        openLink()
    }

    private fun scheduleReconnect() {
        if (!running) return
        main.removeCallbacks(reconnect)
        main.postDelayed(reconnect, backoff(failures))
        failures++
    }

    /** Exponential, capped: a server that is off overnight must not be a radio. */
    private fun backoff(attempts: Int): Long =
        (BACKOFF_BASE_MS shl attempts.coerceAtMost(5)).coerceAtMost(BACKOFF_MAX_MS)

    /**
     * The same curve with a much lower ceiling.
     *
     * A socket and a microphone fail for opposite reasons. An unreachable server
     * is usually off and may be off all night, so a minute between attempts is
     * right. A recorder that could not be opened is almost always held by
     * something with a *short* life — a voice note, an alarm, another
     * assistant's one-shot — and a minute of not listening after a two-second
     * conflict is most of what "the wake word stops working" was.
     */
    private fun micBackoff(attempts: Int): Long =
        (BACKOFF_BASE_MS shl attempts.coerceAtMost(3)).coerceAtMost(MIC_BACKOFF_MAX_MS)

    private fun hasMic(): Boolean =
        checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    // --- AssistPipelineClient.Callbacks --------------------------------------

    override fun onWakeWord(name: String) {
        Log.i(TAG, "wake word heard")
        failures = 0
        micFailures = 0
        // Hand the conversation on and get out of the way; this service keeps
        // the mic only until something else takes it.
        pause()

        // The screen may well be off — that is the whole point of a wake word —
        // and an orb drawn on a dark panel is not an orb anybody sees. Taken
        // before the overlay goes up, released when the conversation ends.
        wakeTheScreen()

        // Asked ONCE, and asked BEFORE anything opens a microphone.
        //
        // An overlay window is not drawn above the keyguard: `TYPE_APPLICATION_
        // OVERLAY` is a non-Activity window, and the platform stopped honouring
        // FLAG_SHOW_WHEN_LOCKED for those long before this app's minimum API.
        // So on a locked phone the overlay conversation is a conversation
        // nobody can see — and the full-screen intent brings up
        // `JarvisAssistActivity`, which starts a conversation of its OWN.
        //
        // Two conversations means two `AudioRecord`s on one microphone, and the
        // outcome of that is decided by the platform rather than by this code:
        // the second `startRecording` wins and the first goes silent, or it
        // fails outright. Either way one of the two Jarvises on screen is deaf,
        // and which one is a race with the activity's ACTION_PAUSE. That is a
        // plausible reading of "it wasn't really able to hear me" from a phone
        // sitting locked on a desk, which is the position a wake word is FOR.
        //
        // The activity is the half that is actually visible when locked, so it
        // gets the microphone, and the overlay is not attempted at all.
        val locked = isLocked()
        val showedOverlay = !locked && startOverlayConversation()
        if (showedOverlay) {
            // The orb is on screen over whatever the user is doing, and the
            // conversation is running in this process. Nothing to hand off to,
            // no re-arm timer: onIdle gives the mic back itself.
            return
        }

        // Locked, or no overlay permission. The full-screen intent is the
        // platform's own mechanism for putting something in front of a locked
        // phone — it is how an incoming call gets on screen — and
        // `JarvisAssistActivity` is declared `showWhenLocked` + `turnScreenOn`
        // for exactly this.
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
        //
        // Not armed when the overlay took the conversation: that path owns the
        // microphone and hands it back from onIdle, and a re-arm firing under
        // it would take the mic away mid-sentence.
        if (!showedOverlay) {
            main.removeCallbacks(rearm)
            main.postDelayed(rearm, HANDOFF_GRACE_MS)
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

    // --- the floating orb -----------------------------------------------------

    /**
     * Show the Siri-style orb over whatever is on screen and talk there.
     *
     * @return false when there is no overlay to show — the permission is not
     *   granted, or the window manager refused it. The caller falls back rather
     *   than assuming a surface that is not there.
     *
     * NO keyguard check *here*. There used to be one, and it was the reason the
     * overlay "still doesn't work": `isKeyguardLocked()` is true whenever the
     * keyguard is up, which on any phone with a secure lock includes the screen
     * simply being OFF — and the answer to being locked is the full-screen
     * intent, which only [onWakeWord] can post. Whether the phone is locked is
     * therefore a decision about WHICH surface gets the microphone, and it
     * belongs to the caller that owns both. Putting it back here would make
     * this function's "no" mean two different things again.
     */
    private fun startOverlayConversation(): Boolean {
        if (!AssistOverlay.canShow(this)) return false
        if (!config.isConfigured) return false

        endOverlayConversation(giveMicBack = false)
        val surface = AssistOverlay(this) { endOverlayConversation(giveMicBack = true) }
        if (!surface.attach()) return false
        overlay = surface
        askHost = ConversationAskHost(
            context = this,
            config = config,
            conversation = { convo },
            surface = overlayAskSurface,
        ).also { CompanionMessageHandler.speechHost = it }
        convo = JarvisConversation(
            this, config, overlayUi, inactivityMs = 8000L,
            // The name and the command are one breath, so capture opens inside
            // the sentence. Without this the first buffer becomes the room and
            // the command that follows is never heard.
            speechAlreadyUnderway = true,
        )
            .also { it.start() }
        return true
    }

    /** Take the orb down. [giveMicBack] re-opens the wake link afterwards. */
    private fun endOverlayConversation(giveMicBack: Boolean) {
        val hadOne = overlay != null
        // The host first, and by identity: another surface may have registered
        // since, and clearing the slot unconditionally would leave a live
        // screen unable to be asked anything.
        askHost?.let {
            CompanionMessageHandler.clearSpeechHost(it)
            it.stop()
        }
        askHost = null
        convo?.stop(); convo = null
        overlay?.detach(); overlay = null
        releaseScreen()
        if (hadOne && giveMicBack) resume()
    }

    /** The orb's view of a question, for [ConversationAskHost]. */
    private val overlayAskSurface = object : ConversationAskHost.Surface {
        override val isShowing: Boolean get() = overlay != null

        override fun onMode(mode: JarvisOrbView.Mode, label: String) {
            overlay?.setMode(mode, label)
        }

        override fun onAmplitude(level: Float) {
            overlay?.setAmplitude(level)
        }

        // A question is Jarvis talking, so it goes where the reply goes; the
        // answer goes where the user's own words go. Putting them the other way
        // round reads as the orb asking itself something.
        override fun onQuestion(text: String) {
            overlay?.setResponse(text)
            overlay?.setTranscript("")
        }

        override fun onAnswerTranscript(text: String) {
            overlay?.setTranscript(text)
        }

        // The orb has no talk button on it, so a state word is right here.
        override fun onResting() {
            overlay?.setMode(JarvisOrbView.Mode.IDLE, "LISTENING")
        }
    }

    /**
     * The conversation's view of the overlay.
     *
     * An anonymous object rather than another interface on the service:
     * `JarvisConversation.Ui` and `AssistPipelineClient.Callbacks` both declare
     * `onTranscript(String)` and `onError(String)`, so implementing both here
     * would collapse two unrelated meanings into one method.
     */
    private val overlayUi = object : JarvisConversation.Ui {
        override fun onMode(mode: JarvisOrbView.Mode, label: String) {
            overlay?.setMode(mode, label)
        }

        override fun onAmplitude(level: Float) {
            overlay?.setAmplitude(level)
        }

        override fun onTranscript(text: String) {
            overlay?.setTranscript(text)
        }

        override fun onResponse(text: String) {
            overlay?.setResponse(text)
        }

        override fun onError(message: String) {
            overlay?.setResponse(message)
            overlay?.setMode(JarvisOrbView.Mode.ERROR, "ERROR")
            // Leave it up for a moment so the reason can be read, then go back
            // to listening. An error that vanishes instantly is a wake word
            // that appears to do nothing.
            main.postDelayed({ endOverlayConversation(giveMicBack = true) }, ERROR_LINGER_MS)
        }

        override fun onTools(run: ToolRun) {
            overlay?.setTools(run)
        }

        override fun onIdle() {
            endOverlayConversation(giveMicBack = true)
        }
    }

    /**
     * Light the screen, so there is something to draw the orb on.
     *
     * A wake word is for the phone you are not holding, which usually means a
     * screen that is off. `FLAG_TURN_SCREEN_ON` on the overlay window covers
     * the common case; this covers the rest, and it is deliberately a short
     * timed lock rather than one this service has to remember to release —
     * a foreground service that holds the screen on because a code path
     * returned early is a dead battery by morning.
     */
    private fun wakeTheScreen() {
        try {
            val power = getSystemService(PowerManager::class.java) ?: return
            @Suppress("DEPRECATION") // No non-deprecated way to wake the screen.
            val lock = power.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,
                "jarvis:wake-word",
            )
            lock.acquire(SCREEN_WAKE_MS)
            screenLock?.let { runCatching { if (it.isHeld) it.release() } }
            screenLock = lock
        } catch (t: Throwable) {
            Log.w(TAG, "could not turn the screen on", t)
        }
    }

    private fun releaseScreen() {
        screenLock?.let { runCatching { if (it.isHeld) it.release() } }
        screenLock = null
    }

    private fun isLocked(): Boolean =
        (getSystemService(KeyguardManager::class.java))?.isKeyguardLocked == true

    // --- the notification ----------------------------------------------------

    /** @return false if the platform refused, in which case we are stopping. */
    private fun enterForeground(): Boolean {
        ensureChannel()
        return try {
            ServiceCompat.startForeground(
                this,
                NOTIFICATION_ID,
                buildNotification(WAITING, tapToRestart = false),
                // The honest type, and the one the platform requires before it
                // will let a background service touch the mic at all on 34+.
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
            )
            true
        } catch (t: Throwable) {
            // On 12+ this is where a background start lands: the service was
            // created and the platform then refused to let it be foreground.
            // The user gets the same one-tap repair as any other refusal —
            // and the caller must not carry on opening a microphone behind a
            // notification that was never accepted.
            Log.w(TAG, "could not enter the foreground", t)
            tellTheUser(this, WakeStartPolicy.Route.NEEDS_A_TAP)
            stopSelf()
            false
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

    private fun buildNotification(text: String, tapToRestart: Boolean): Notification {
        val open = PendingIntent.getActivity(
            this,
            0,
            if (tapToRestart) {
                ListenTrampolineActivity.intent(this)
            } else {
                Intent(this, JarvisAssistActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            },
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

    private fun updateNotification(text: String, tapToRestart: Boolean = false) {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        manager.notify(NOTIFICATION_ID, buildNotification(text, tapToRestart))
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
     *
     * Only used when the floating orb is unavailable; with "display over other
     * apps" granted and the phone unlocked, the user never sees this.
     */
    private fun showHeard(open: Intent) {
        ensureAlertChannel()
        val full = PendingIntent.getActivity(
            this,
            2,
            open,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        // Whether the takeover will actually happen, asked before claiming it.
        // Android 14 grants USE_FULL_SCREEN_INTENT at install only to calling
        // and alarm apps; everyone else holds the permission, is silently
        // downgraded to a heads-up, and gets a notification that waits in the
        // shade for a tap. That is the reported symptom, and a wake word that
        // says "heard you" while doing nothing visible is worse than one that
        // says what is stopping it.
        val willTakeOver = GrapheneCompat.canUseFullScreenIntent(this)
        val note = Notification.Builder(this, CHANNEL_ALERT)
            .setSmallIcon(R.drawable.ic_jarvis_status)
            .setContentTitle("Jarvis is listening")
            .setContentText(
                if (willTakeOver) "Heard you — tap to talk"
                else "Heard you — tap to talk. Turn on “display over other apps” " +
                    "to have this open by itself."
            )
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

    companion object {
        private const val TAG = "JarvisWake"
        private const val CHANNEL = "jarvis-wake"
        private const val CHANNEL_ALERT = "jarvis-wake-heard"
        private const val CHANNEL_ATTENTION = "jarvis-wake-attention"
        private const val NOTIFICATION_ID = 0x4A57 // 'JW'
        private const val ALERT_ID = 0x4A58 // 'JX'
        private const val ATTENTION_ID = 0x4A59 // 'JY'

        const val ACTION_STOP = "ai.jarvis.app.WAKE_STOP"
        const val ACTION_PAUSE = "ai.jarvis.app.WAKE_PAUSE"
        const val ACTION_RESUME = "ai.jarvis.app.WAKE_RESUME"

        private const val WAITING = "Say “Hey Jarvis” at any time"

        /**
         * Said when detection is local. Worth distinguishing: the difference
         * between the two states is whether a continuous recording of the room
         * is being uploaded, and the notification is the only place the user
         * ever sees which one they are in.
         */
        private const val WAITING_LOCAL =
            "Say “Hey Jarvis” — heard on this phone, nothing is sent until then"

        /** What the local detector reports, matching the server's model name. */
        private const val LOCAL_WAKE_WORD = "hey_jarvis"
        private const val WAITING_PAUSED = "Paused while you are talking"

        /**
         * Said while somebody is on a call.
         *
         * Distinguished from the ordinary pause because the two are fixed by
         * different things: one ends when the user stops talking to Jarvis, the
         * other when they stop talking to a person.
         */
        private const val WAITING_IN_CALL = "Paused for your call — back when it ends"

        /**
         * How long a wake word gets to become a conversation before the mic is
         * taken back. Long enough to walk to the phone and tap the heads-up.
         */
        private const val HANDOFF_GRACE_MS = 30_000L

        /**
         * How long the screen is forced on for a wake word. Long enough to see
         * the orb arrive and start talking; short enough that a detection
         * nobody followed up on costs seconds of screen, not a night of it.
         */
        private const val SCREEN_WAKE_MS = 15_000L

        /** How long a failed turn stays readable on the floating orb. */
        private const val ERROR_LINGER_MS = 2_600L

        private const val BACKOFF_BASE_MS = 2_000L
        private const val BACKOFF_MAX_MS = 60_000L

        /** See [micBackoff]. A recorder conflict is short; the wait should be. */
        private const val MIC_BACKOFF_MAX_MS = 16_000L

        /**
         * How long to wait before looking at the audio mode again during a call,
         * on a device that will also be TOLD when it ends.
         *
         * A safety net against a missed callback, nothing more: on API 31+ the
         * edge arrives first and this is cancelled before it fires.
         */
        private const val CALL_RECHECK_MS = 60_000L

        /**
         * The same wait on a device with no mode callback (below API 31), where
         * looking is the only way the end of a call is ever noticed.
         *
         * Short, because it is the mechanism rather than the backstop, and a
         * poll of the audio mode is one binder call — several orders of
         * magnitude cheaper than the microphone it is deciding whether to open.
         */
        private const val CALL_POLL_MS = 10_000L

        /**
         * How often to check that the listener is actually up.
         *
         * Inexact and batched with whatever else the phone is doing, so the
         * real interval is "roughly this, when the device is awake anyway".
         * Fifteen minutes is the shortest period Android will honour for
         * background work without special pleading, and there is nothing to
         * gain from asking for more: this exists to recover from a kill or a
         * refused start, both of which the user can wait a few minutes for.
         */
        private const val HEARTBEAT_MS = 15 * 60 * 1000L

        /**
         * Start listening if the user has asked for it.
         *
         * Safe to call repeatedly and from anywhere — it checks the setting
         * itself rather than trusting the caller to. What it will *not* do is
         * fail silently: when the platform would refuse the start,
         * [tellTheUser] puts a one-tap repair in the shade instead.
         *
         * @param fromForeground true when the caller is a resumed Activity, for
         *   which a start is always permitted. Callers that are not one (a boot
         *   receiver, the heartbeat) must leave this false or the policy check
         *   is a lie and the start throws.
         * @return what was decided, which the tests assert on and callers may
         *   ignore.
         */
        fun ensureRunning(context: Context, fromForeground: Boolean = false): WakeStartPolicy.Route {
            val app = context.applicationContext
            val config = JarvisConfig(app)
            val route = WakeStartPolicy.route(
                enabled = config.wakeWordEnabled,
                hasMicPermission = app.checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                    PackageManager.PERMISSION_GRANTED,
                fromForeground = fromForeground,
                sdkInt = Build.VERSION.SDK_INT,
                ignoringBatteryOptimizations = isExemptFromDoze(app),
                canDrawOverlays = Settings.canDrawOverlays(app),
            )
            when (route) {
                WakeStartPolicy.Route.OFF -> cancelHeartbeat(app)
                WakeStartPolicy.Route.DIRECT -> {
                    armHeartbeat(app)
                    try {
                        context.startForegroundService(Intent(context, WakeWordService::class.java))
                    } catch (t: Throwable) {
                        // The policy said this would be allowed and it was not.
                        // Rather than swallow it — which is how the listener
                        // came to be silently dead after every reboot — fall
                        // back to the thing the user can act on.
                        Log.w(TAG, "the wake listener would not start", t)
                        tellTheUser(app, WakeStartPolicy.Route.NEEDS_A_TAP)
                    }
                }
                WakeStartPolicy.Route.NEEDS_A_TAP,
                WakeStartPolicy.Route.NEEDS_MIC_PERMISSION,
                -> {
                    armHeartbeat(app)
                    tellTheUser(app, route)
                }
            }
            return route
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

        private fun isExemptFromDoze(context: Context): Boolean = try {
            context.getSystemService(PowerManager::class.java)
                ?.isIgnoringBatteryOptimizations(context.packageName) == true
        } catch (t: Throwable) {
            false
        }

        // --- the one-tap repair ----------------------------------------------

        /**
         * Say that Jarvis wants to listen and cannot, with a tap that fixes it.
         *
         * The alternative — which is what shipped — was a `Log.w`. On a phone
         * that means nothing happened and nothing said so, which is how "the
         * mic is always on" turned into "I have to open the app and start it".
         *
         * Idempotent: the same notification id and `setOnlyAlertOnce`, so the
         * quarter-hourly heartbeat re-posting it is silent after the first
         * time.
         */
        fun tellTheUser(context: Context, route: WakeStartPolicy.Route) {
            val message = WakeStartPolicy.explain(route) ?: return
            val manager = context.getSystemService(NotificationManager::class.java) ?: return
            if (Build.VERSION.SDK_INT >= 26 &&
                manager.getNotificationChannel(CHANNEL_ATTENTION) == null
            ) {
                manager.createNotificationChannel(
                    NotificationChannel(
                        CHANNEL_ATTENTION,
                        "Jarvis needs a tap",
                        NotificationManager.IMPORTANCE_DEFAULT,
                    ).apply {
                        description =
                            "Shown when always-on listening is switched on but Android will " +
                                "not let it start on its own — after a restart, for example."
                        setShowBadge(true)
                    }
                )
            }
            val tap = PendingIntent.getActivity(
                context,
                3,
                ListenTrampolineActivity.intent(context),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            manager.notify(
                ATTENTION_ID,
                Notification.Builder(context, CHANNEL_ATTENTION)
                    .setSmallIcon(R.drawable.ic_jarvis_status)
                    .setContentTitle("Jarvis is not listening")
                    .setContentText(message)
                    .setStyle(Notification.BigTextStyle().bigText(message))
                    .setContentIntent(tap)
                    .setAutoCancel(true)
                    .setOnlyAlertOnce(true)
                    .setCategory(Notification.CATEGORY_STATUS)
                    .build()
            )
        }

        /** Take the repair notification down once listening actually started. */
        fun clearAttention(context: Context) {
            context.getSystemService(NotificationManager::class.java)?.cancel(ATTENTION_ID)
        }

        // --- the heartbeat -----------------------------------------------------

        /**
         * Re-check every quarter of an hour that the listener is running.
         *
         * `START_STICKY` is not enough on its own: the restart the system
         * performs after a kill is itself a background start of a
         * microphone-typed service, so on 12+ the platform may refuse the very
         * restart it just scheduled. An alarm gives a second chance that is not
         * subject to the same race, and — since [ensureRunning] falls through
         * to [tellTheUser] — turns a permanent silent failure into a visible
         * one at worst.
         */
        fun armHeartbeat(context: Context) {
            val alarms = context.getSystemService(AlarmManager::class.java) ?: return
            try {
                alarms.setInexactRepeating(
                    AlarmManager.ELAPSED_REALTIME,
                    SystemClock.elapsedRealtime() + HEARTBEAT_MS,
                    HEARTBEAT_MS,
                    heartbeatIntent(context),
                )
            } catch (t: Throwable) {
                Log.w(TAG, "could not arm the listener heartbeat", t)
            }
        }

        fun cancelHeartbeat(context: Context) {
            val alarms = context.getSystemService(AlarmManager::class.java) ?: return
            try {
                alarms.cancel(heartbeatIntent(context))
            } catch (t: Throwable) {
                Log.w(TAG, "could not cancel the listener heartbeat", t)
            }
        }

        private fun heartbeatIntent(context: Context): PendingIntent = PendingIntent.getBroadcast(
            context.applicationContext,
            4,
            Intent(context.applicationContext, WakeHeartbeatReceiver::class.java)
                .setAction(WakeHeartbeatReceiver.ACTION_CHECK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }
}
