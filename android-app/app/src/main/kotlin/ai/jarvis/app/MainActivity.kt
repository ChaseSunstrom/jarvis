package ai.jarvis.app

import ai.jarvis.app.assist.JarvisConversation
import ai.jarvis.app.companion.CompanionMessageHandler
import ai.jarvis.app.companion.ConversationAskHost
import ai.jarvis.app.assist.ActivityRows
import ai.jarvis.app.assist.KnowledgeGraph
import ai.jarvis.app.assist.ToolActivityView
import ai.jarvis.app.assist.ToolRun
import ai.jarvis.app.assist.WakeStartPolicy
import ai.jarvis.app.assist.WakeWordService
import ai.jarvis.app.automation.JarvisAutomationService
import ai.jarvis.app.compat.GrapheneCompat
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisBootAnimation
import ai.jarvis.app.ui.ConsoleFrame
import ai.jarvis.app.ui.ConsoleTab
import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.ActivityStrip
import ai.jarvis.app.ui.KnowledgeGraphView
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.SystemCheckActivity
import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * The Jarvis home — the face of the app. Opening it lands on the orb, not a
 * dashboard: it is already listening, and MANAGE is the way to everything else.
 *
 * **There is no talk button.** *"is there a way you can get rid of the push to
 * talk button ... and have it just listen for the wake word? or when the mobile
 * app is open directly, to constantly listen"* — so opening this screen IS the
 * activation gesture. [resumeHandsFree] starts a [continuous]
 * [JarvisConversation] on every resume and [releaseTheMic] ends it on every
 * pause, which makes the microphone's lifetime exactly the lifetime of a screen
 * the user is looking at. That is the property that makes an always-open mic
 * defensible: it is open while its own UI is in front of you, saying so.
 *
 * The pill that used to say TAP TO SPEAK is a mute, because an always-on
 * microphone with no off switch is not a thing to ship. Muting is remembered
 * ([JarvisConfig.micMuted]) — a kill switch that forgets is not one.
 *
 * When this screen is NOT in front of you, "Hey Jarvis" is the way in, via
 * [WakeWordService]. The two never hold the microphone at once: the wake
 * listener is paused for as long as this screen owns it, because two
 * AudioRecords on one device is a coin toss over which gets the audio.
 *
 * The conversation itself is [JarvisConversation], the same engine the assist
 * popup uses, so the two behave identically down to the barge-in timing.
 *
 * On a **cold start** the screen opens with the power-on sequence
 * ([JarvisBootAnimation]) playing over this exact layout: same orb object, same
 * position, controls faded out until the sequence hands them back. On a warm
 * resume, a rotation, or a return from Settings, none of that happens — see
 * [JarvisApp.consumeColdStart].
 */
class MainActivity : Activity(), JarvisConversation.Ui {

    private lateinit var orbView: JarvisOrbView
    private lateinit var transcriptView: TextView
    private lateinit var responseView: TextView
    private lateinit var toolActivityView: ToolActivityView
    /** The living activity around the reactor (M61): what the house did, as it did it. */
    private lateinit var activityStrip: ActivityStrip
    private lateinit var knowledgeGraphView: KnowledgeGraphView
    private lateinit var muteButton: Button
    private lateinit var listenButton: Button
    private lateinit var listenReason: TextView
    private lateinit var config: JarvisConfig

    /** Everything that is not the orb: transcript, reply, controls, nav. */
    private lateinit var homeControls: LinearLayout
    private lateinit var root: FrameLayout
    private lateinit var bannerSlot: FrameLayout

    private var convo: JarvisConversation? = null

    /**
     * Lets Jarvis ask a question on this screen instead of starting another
     * activity over the top of it. See [ConversationAskHost].
     */
    private var askHost: ConversationAskHost? = null
    private var boot: JarvisBootAnimation? = null

    /**
     * The console's bar, less its tabs: the mark, JARVIS and the readout.
     * The voice screen in a browser sits under the same `TopBar`; on the phone
     * the brand used to be a wordmark painted over the reactor, and the state
     * was only the caption. The readout says the state now, as the console's
     * says STANDBY beside its dot.
     */
    private lateinit var brandBar: ConsoleFrame.Brand

    /** True between [onResume] and [onPause]: whether this screen may hold the mic. */
    private var inForeground = false

    /**
     * How long to wait before opening the mic again after a conversation ended
     * badly, in ms. Zero when the last one ended cleanly.
     *
     * Hands-free means [onIdle] re-opens the microphone, and an unreachable
     * server fails in well under a second — so without this, a wrong URL is an
     * unbounded reconnect loop that heats the phone and rewrites the error
     * message faster than it can be read. Doubles per consecutive failure and
     * is cleared by any conversation that reaches LISTENING.
     */
    private var restartBackoffMs = 0L

    /** The pending hands-free restart, so pausing can cancel one mid-flight. */
    private val restart = Runnable { resumeHandsFree() }
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())

    /** Set before the layout is built, so the orb knows not to play its entrance. */
    private var coldStart = false

    /** One notification prompt per launch. See [askForNotificationsOnce]. */
    private var askedForNotifications = false

    /** True while the power-on is playing. */
    private val booting: Boolean get() = boot != null

    override fun onCreate(savedInstanceState: Bundle?) {
        // A saved instance state means a rotation or a process restore, not a
        // launch — the boot sequence belongs to a genuinely cold start only.
        coldStart = savedInstanceState == null && JarvisApp.consumeColdStart(this)

        super.onCreate(savedInstanceState)
        // Before setContentView: the listener has to be in place before the
        // first frame, or the platform runs its own exit animation instead.
        installSplashHandoff()
        JarvisUi.immersive(this)
        config = JarvisConfig(this)
        setContentView(buildUi())
        showIdle()
        if (coldStart) startBootAnimation()
    }

    override fun onResume() {
        super.onResume()
        inForeground = true
        askForNotificationsOnce()
        startAutomationLayer()
        // Settings may have changed the server behind our back.
        if (convo?.isRunning != true && !booting) showIdle()
        // Not while the power-on is playing. The banner sits inside
        // homeControls, which is at alpha 0 until the handoff, so the seven
        // binder round-trips behind missingEssentials() would buy an invisible
        // view on the cold-start critical path. The sequence's onComplete
        // refreshes it the moment there is something to see.
        if (!booting) refreshStatusBanner()
        if (::listenButton.isInitialized) refreshListening()
        openSystemCheckOnceIfSetupIsIncomplete()
        resumeHandsFree()
    }

    /**
     * Leaving the screen closes the microphone. Every time, no exceptions.
     *
     * `onPause` rather than `onStop`: the moment another window is in front of
     * this one — a dialog, the recents switcher, the notification shade pulled
     * down over it — the user is no longer looking at the screen that says
     * LISTENING, and an open mic behind somebody else's UI is the thing this
     * design must never do. It costs a re-open when they come back, which is
     * a socket and a couple of hundred milliseconds.
     */
    override fun onPause() {
        inForeground = false
        releaseTheMic()
        super.onPause()
    }

    /**
     * Open the microphone, if this screen is allowed to have it.
     *
     * Every "no" here is silent and ordinary rather than an error: not
     * configured yet, no permission granted, muted, or the power-on still
     * playing. The screen already says what is missing — the setup banner, the
     * mute pill's own label — and a second voice saying it in the response
     * field would be noise on the first screen of the app.
     */
    private fun resumeHandsFree() {
        handler.removeCallbacks(restart)
        if (!inForeground || booting) return
        if (convo?.isRunning == true) return
        if (!config.isConfigured || config.micMuted) return
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) return

        // The wake listener goes quiet first and is only resumed in
        // releaseTheMic(), so the two never contend. Ordering matters: pausing
        // after the conversation has opened its recorder is the race, not the
        // fix for it.
        runCatching { WakeWordService.pause(this) }
        transcriptView.text = ""
        responseView.text = ""
        toolActivityView.hide()
        convo = JarvisConversation(
            this,
            config,
            this,
            inactivityMs = DEAF_CHECK_MS,
            continuous = true,
        ).also { it.start() }
        askHost = ConversationAskHost(
            context = this,
            config = config,
            conversation = { convo },
            surface = askSurface,
        ).also { CompanionMessageHandler.speechHost = it }
        refreshMuteButton()
    }

    /** This screen's view of a question, for [ConversationAskHost]. */
    private val askSurface = object : ConversationAskHost.Surface {
        override val isShowing: Boolean get() = inForeground && !isFinishing

        override fun onMode(mode: JarvisOrbView.Mode, label: String) {
            orbView.setMode(mode)
            orbView.setStateLabel(label)
        }

        override fun onAmplitude(level: Float) = orbView.setAmplitude(level)

        override fun onQuestion(text: String) {
            responseView.text = text
            transcriptView.text = ""
        }

        override fun onAnswerTranscript(text: String) {
            transcriptView.text = text
        }

        // This screen has a talk button on it, so showIdle() owns the wording —
        // an instruction from here would sit next to the button that already
        // says it.
        override fun onResting() = showIdle()
    }

    /**
     * Give the microphone back — to the wake listener, or to whatever else on
     * the phone wants it.
     */
    private fun releaseTheMic() {
        handler.removeCallbacks(restart)
        askHost?.let { CompanionMessageHandler.clearSpeechHost(it); it.stop() }
        askHost = null
        convo?.stop()
        convo = null
        // Only if it is supposed to be running at all; resuming a listener the
        // user has switched off would turn leaving this screen into a way to
        // switch it back on.
        if (config.wakeWordEnabled) runCatching { WakeWordService.resume(this) }
        if (::muteButton.isInitialized) refreshMuteButton()
    }

    /**
     * Take the user to the checklist the first time something essential is off.
     *
     * A banner at the bottom of the home screen was not enough, and the field
     * report says why: "the overlay isn't popping up still, only the
     * notification". Every one of those symptoms is a special access that has
     * to be granted on a Settings screen the user has never heard of, and an
     * app that waits to be asked about it is an app that stays broken. The
     * checklist already names each one, says what breaks without it and opens
     * the exact page — it just needed to be somewhere other than behind a
     * button nobody presses.
     *
     * Once per install, and only when something ESSENTIAL is missing, so this
     * is a setup step rather than a nag. The banner covers every later change.
     */
    private fun openSystemCheckOnceIfSetupIsIncomplete() {
        if (config.setupChecklistShown) return
        if (GrapheneCompat.missingEssentials(this).isEmpty()) {
            // Nothing to walk them through — and remember that, so a permission
            // revoked in six months does not trigger a first-run flow.
            config.setupChecklistShown = true
            return
        }
        config.setupChecklistShown = true
        runCatching { openSystemCheck() }
    }

    /**
     * Ask for POST_NOTIFICATIONS, which nothing ever did.
     *
     * It is declared in the manifest and has been since the app was written,
     * which on Android 12 and below is the whole story. On 13+ it is a runtime
     * permission, and a runtime permission nobody requests is a runtime
     * permission you do not have — so on a modern phone Jarvis could not show
     * the listening notification, the "heard you" alert, or a Tier-3 approval.
     * The last one matters most: an approval that cannot be delivered times out
     * and is denied, so the app looked like it was ignoring the user.
     *
     * Asked from the home screen rather than at the first notification, because
     * this is an ordinary Activity that can hold the round trip — and asked
     * once per launch at most, because a dialog that reappears on every resume
     * is how people learn to hit Deny.
     */
    private fun askForNotificationsOnce() {
        if (Build.VERSION.SDK_INT < 33 || askedForNotifications) return
        if (checkSelfPermission(POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        askedForNotifications = true
        runCatching { requestPermissions(arrayOf(POST_NOTIFICATIONS), REQ_NOTIFICATIONS) }
    }

    /**
     * Bring the automation layer up from a foreground path.
     *
     * Until this existed, `JarvisAutomationService` was started only by
     * `BootReceiver` on BOOT_COMPLETED / MY_PACKAGE_REPLACED, or by a manifest
     * broadcast that Android 12+ refuses outright (the receiver says so
     * itself). So a fresh install had no `AutomationRuntime`, which means no
     * `AutomationBridge.dispatcher` and no `ActionEnv` — and every command from
     * the server would have been answered "unsupported" even once there was a
     * socket to receive one on — until the phone was next rebooted.
     *
     * Starting a foreground service from a resumed Activity is always
     * permitted; the guard is here because a refused start must not take the
     * home screen down with it.
     */
    private fun startAutomationLayer() {
        runCatching { JarvisAutomationService.ensureRunning(this, "home") }
        // Starting from a resumed activity is always permitted — the one route
        // Android never refuses for a microphone-typed service — which is why
        // the wake listener is nudged here rather than from a receiver, and why
        // this is the one caller that may claim `fromForeground`.
        runCatching { WakeWordService.ensureRunning(this, fromForeground = true) }
    }

    // --- the power-on -------------------------------------------------------

    /**
     * Android 12+ only. The platform splash otherwise cross-fades out on its
     * own schedule; taking the exit over means the boot sequence starts on the
     * very frame the splash icon leaves, with #04070C behind both, so there is
     * no white flash and no empty frame anywhere on the launch path.
     *
     * Best-effort throughout: a ROM with a broken SplashScreen implementation
     * gets the default exit and the fallback timer below, never a crash.
     */
    private fun installSplashHandoff() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        try {
            splashScreen.setOnExitAnimationListener { splashView ->
                // Remove it immediately rather than animating: the boot
                // sequence's first frame is black, which is what the splash
                // was already showing.
                try {
                    splashView.remove()
                } catch (t: Throwable) {
                    // Nothing to do; the platform will tear it down itself.
                }
                boot?.start()
            }
        } catch (t: Throwable) {
            // No splash handoff on this ROM. The fallback timer covers it.
        }
    }

    /**
     * Add the power-on overlay over the finished home layout and arrange for it
     * to start.
     *
     * Below API 31 there is no splash exit to wait for, so it starts now. At or
     * above it, the splash listener starts it — with a timer as a backstop,
     * because a listener that never fires would leave the user looking at a
     * black screen forever. [JarvisBootAnimation.start] is idempotent, so
     * whichever path arrives first wins and the other is a no-op.
     */
    private fun startBootAnimation() {
        val animation = JarvisBootAnimation(this).apply {
            orb = orbView
            actionCount = JarvisBootAnimation.lastActionCount(this@MainActivity)
            onHomeAlpha = { a ->
                homeControls.alpha = a
                brandBar.alpha = a
            }
            onComplete = {
                boot = null
                homeControls.alpha = 1f
                brandBar.alpha = 1f
                showIdle()
                refreshStatusBanner()
                // And open the microphone, which onResume declined to do while
                // this was playing. Without it a COLD start — the launch from
                // the home screen, the commonest way into this app — is the one
                // case that ends up not listening, because the only other
                // caller is a resume that already happened.
                resumeHandsFree()
            }
        }
        boot = animation
        homeControls.alpha = 0f
        brandBar.alpha = 0f
        root.addView(
            animation,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        // Waiting for the splash exit only buys a seamless handoff if there is
        // something to hand off to. With animations off the sequence collapses
        // to its end state, so waiting would just hold the home UI at alpha 0
        // for as long as the splash takes — a black screen for the one user who
        // explicitly asked for no animation.
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || !animation.willPlay()) {
            animation.start()
        } else {
            root.postDelayed({ boot?.start() }, SPLASH_FALLBACK_MS)
        }
    }

    // --- layout -------------------------------------------------------------

    private fun buildUi(): ViewGroup {
        root = FrameLayout(this).apply { setBackgroundColor(JarvisUi.BG) }

        orbView = JarvisOrbView(this).apply {
            chromeEnabled = true
            // A STATUS word, not an instruction. The instruction is the pill
            // below, which is the only thing that actually starts a turn — see
            // showIdle().
            setStateLabel(IDLE_CAPTION)
            // On a cold start the boot sequence drives this orb from a point;
            // playing the entrance underneath would fight the ignition.
            if (coldStart) beginBoot() else startEntrance()
        }
        root.addView(
            orbView,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )

        brandBar = ConsoleFrame.brand(this).apply { setStatus(IDLE_CAPTION, ConsoleFrame.Tone.NEUTRAL) }
        root.addView(
            brandBar,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP
            )
        )

        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            val m = JarvisUi.dp(this@MainActivity, JarvisUi.Space.WIDE)
            setPadding(m, 0, m, JarvisUi.dp(this@MainActivity, JarvisUi.Size.SHEET))
        }
        homeControls = col

        bannerSlot = FrameLayout(this)
        transcriptView = JarvisUi.transcriptView(this)
        responseView = JarvisUi.responseView(this)
        toolActivityView = ToolActivityView(this)
        activityStrip = ActivityStrip(this)
        knowledgeGraphView = KnowledgeGraphView(this)
        muteButton = JarvisUi.button(this, "LISTENING — TAP TO MUTE") { toggleMute() }

        // The always-on listener's actual state, on the screen the user opens.
        //
        // Reported three times as "I have to select start listening in the app
        // before it works". There was nothing here to select: the only control
        // was a switch in Settings behind a SAVE button, and nothing anywhere
        // said whether the listener was running. So the app both LOOKED
        // stateless and gave no way to change the state — and every diagnosis
        // of it was guesswork about somebody else's phone.
        listenButton = JarvisUi.button(this, "…") { toggleListening() }
        listenReason = TextView(this).apply {
            setTextColor(JarvisUi.DIM)
            textSize = JarvisUi.Type.LABEL
            gravity = Gravity.CENTER
            setPadding(0, JarvisUi.dp(this@MainActivity, JarvisUi.Space.TIGHT), 0, 0)
        }

        // One way in to everything that is not this screen.
        //
        // This was six buttons — the console's five sections plus PHONE — which
        // was itself a fix for three buttons that went to unrelated places. But
        // *"the buttons on the home screen take you to basically the web app
        // view, why dont you just have a MANAGE button"*: the console frame
        // already carries the very same nav as a tab strip, so the home screen
        // was drawing a second copy of somebody else's navigation and had to be
        // kept in step with it by a parity test. Six buttons that open one
        // screen at six scroll positions is one button.
        //
        // PHONE went with them, and did not simply move: it is a tab in that
        // strip now (see ConsoleTab.PHONE and ManagementActivity), so the
        // phone's own settings and the house's sit in one frame wearing the
        // same chrome instead of being a native screen off to one side.
        val nav = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(0, JarvisUi.dp(this@MainActivity, JarvisUi.Space.SECTION), 0, 0)
            addView(JarvisUi.primary(this@MainActivity, "MANAGE") { openConsole(ConsoleTab.DEFAULT) })
            addView(
                android.view.View(this@MainActivity),
                LinearLayout.LayoutParams(JarvisUi.dp(this@MainActivity, JarvisUi.Space.ROW), 1)
            )
            // PHONE, back on the home screen — and not as a walking-back of the
            // dedup that removed the other five.
            //
            // Those five were a second copy of the console's nav. This one is
            // not: it is the half of the app a web page CANNOT be — the
            // microphone, the permissions, the wake word, which server this
            // handset talks to. It went into the console's tab strip, where it
            // was the sixth of six monospace labels on a strip too narrow for
            // six, and it has now been reported missing twice by somebody
            // holding the phone.
            //
            // Two buttons is not a grid, and the thing people open the app to
            // change should not be two taps and a horizontal scroll away.
            addView(
                JarvisUi.button(this@MainActivity, ConsoleTab.PHONE_LABEL) { openSettings() }
            )
        }

        col.addView(
            bannerSlot,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = JarvisUi.dp(this@MainActivity, JarvisUi.Space.SECTION) }
        )
        col.addView(
            toolActivityView,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = JarvisUi.dp(this@MainActivity, JarvisUi.Space.ROW) }
        )
        col.addView(
            activityStrip,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = JarvisUi.dp(this@MainActivity, JarvisUi.Space.ROW) }
        )
        // What Jarvis knows, drawn as the console draws it (M61): hidden until
        // there is a note or a memory to draw.
        col.addView(
            knowledgeGraphView,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = JarvisUi.dp(this@MainActivity, JarvisUi.Space.ROW) }
        )
        col.addView(transcriptView)
        col.addView(responseView)
        col.addView(
            muteButton,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = JarvisUi.dp(this@MainActivity, JarvisUi.Space.SCREEN) }
        )
        col.addView(
            listenButton,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = JarvisUi.dp(this@MainActivity, JarvisUi.Space.ROW) }
        )
        col.addView(listenReason, fullWidthParams())
        col.addView(nav, fullWidthParams())

        root.addView(
            col,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM
            )
        )
        return root
    }

    private fun fullWidthParams() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT
    )

    // --- the always-on listener ---------------------------------------------

    /**
     * Say whether Jarvis is listening for its name, and why not when it is not.
     *
     * Every part of this is a sentence somebody needed and did not have. "Off"
     * is a setting they can turn on from here. "On but Android will not let it
     * start" names the grant that fixes it. "On but no microphone permission"
     * is not the same problem and does not send them to the same screen.
     */
    private fun refreshListening() {
        val route = WakeStartPolicy.route(
            enabled = config.wakeWordEnabled,
            hasMicPermission = checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED,
            // Asked from a resumed Activity, which is the one caller that may.
            fromForeground = true,
            sdkInt = Build.VERSION.SDK_INT,
            ignoringBatteryOptimizations = GrapheneCompat.isIgnoringBatteryOptimizations(this),
            canDrawOverlays = GrapheneCompat.canDrawOverlays(this),
        )
        val on = config.wakeWordEnabled
        listenButton.text = if (on) "LISTENING — TAP TO STOP" else "START LISTENING"
        listenButton.setTextColor(if (on) JarvisUi.ACCENT else JarvisUi.DIM)
        listenReason.text = when {
            !on -> "Jarvis is not listening for its name."
            route == WakeStartPolicy.Route.NEEDS_MIC_PERMISSION ->
                "Waiting on the microphone permission."
            route == WakeStartPolicy.Route.NEEDS_A_TAP ->
                "On, but Android will not restart it by itself — see SYSTEM CHECK."
            config.wakeWordOnDevice ->
                "Listening on this phone. Nothing is sent until you say the name."
            else -> "Listening. Audio streams to your server, which does the detecting."
        }
    }

    private fun toggleListening() {
        if (config.wakeWordEnabled) {
            config.wakeWordEnabled = false
            runCatching { WakeWordService.cancelHeartbeat(this) }
            runCatching { WakeWordService.clearAttention(this) }
            runCatching { stopService(Intent(this, WakeWordService::class.java)) }
            refreshListening()
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC_FOR_WAKE)
            return
        }
        config.wakeWordEnabled = true
        runCatching { WakeWordService.ensureRunning(this, fromForeground = true) }
        refreshListening()
    }


    // --- the GrapheneOS status banner ---------------------------------------

    /**
     * The one thing the home screen says about its own health, and the only
     * route to [SystemCheckActivity] — deliberately, because a diagnostics
     * screen buried in a menu is a screen nobody finds on the day they need it.
     *
     * Network first: on GrapheneOS the per-app Network toggle can be off while
     * every permission check still reads GRANTED, and the only symptom is that
     * nothing works. That deserves the exact settings path in the banner text.
     * Otherwise, anything else essential that is missing. When everything is in
     * order the banner is not there at all.
     */
    private fun refreshStatusBanner() {
        bannerSlot.removeAllViews()

        val network = GrapheneCompat.networkBanner(this)
        val message = if (network != null) {
            network
        } else {
            val missing = GrapheneCompat.missingEssentials(this)
            when {
                missing.isEmpty() -> return
                missing.size == 1 -> "${missing[0].label} is not granted. ${missing[0].why}"
                else -> missing.joinToString(
                    prefix = "Setup incomplete: ",
                    separator = ", ",
                ) { it.label.lowercase() } + " not granted."
            }
        }

        bannerSlot.addView(
            JarvisUi.banner(this, message, "SYSTEM CHECK") { openSystemCheck() },
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )
    }

    // --- actions ------------------------------------------------------------

    /**
     * The one control on the home screen: close the microphone, or open it.
     *
     * This is where TAP TO SPEAK used to be, and it is deliberately the
     * opposite kind of thing. That button was the only way to be heard; this
     * one is the only way NOT to be. Everything else it used to do — asking for
     * the permission, complaining about an unconfigured server — belongs to the
     * paths that already handle those, so a tap here means one thing.
     */
    private fun toggleMute() {
        // A tap during the power-on means "skip it", exactly as it always did.
        boot?.let { it.skip(); return }

        if (!config.isConfigured) {
            openSettings(); return
        }
        if (!config.micMuted &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            // Unmuted and deaf: the grant is what is actually being asked for.
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC); return
        }

        config.micMuted = !config.micMuted
        restartBackoffMs = 0
        if (config.micMuted) {
            convo?.stop()
            convo = null
            if (config.wakeWordEnabled) runCatching { WakeWordService.resume(this) }
            showIdle()
        } else {
            resumeHandsFree()
        }
        refreshMuteButton()
    }

    /**
     * What the pill says, which is the only place the screen states whether it
     * can hear you.
     *
     * Four distinguishable conditions, because "MUTED" covering all of them is
     * how a phone that cannot hear looks identical to one that has been told
     * not to.
     */
    private fun refreshMuteButton() {
        if (!::muteButton.isInitialized) return
        val granted = checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        muteButton.text = when {
            !config.isConfigured -> "SET UP JARVIS"
            config.micMuted -> "MUTED — TAP TO LISTEN"
            !granted -> "GRANT THE MICROPHONE"
            else -> "LISTENING — TAP TO MUTE"
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC &&
            grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED
        ) resumeHandsFree()
        // A REFUSED microphone has to move the pill too, or the screen goes on
        // claiming LISTENING at somebody who has just said no.
        if (requestCode == REQ_MIC) refreshMuteButton()
        // Either answer changes the checklist, and the banner is the only place
        // the home screen says anything about its own health.
        if (requestCode == REQ_NOTIFICATIONS && !booting) refreshStatusBanner()
        if (requestCode == REQ_MIC_FOR_WAKE &&
            grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED
        ) toggleListening()
    }

    private fun openSettings() =
        startActivity(Intent(this, SettingsActivity::class.java))

    private fun openSystemCheck() =
        startActivity(Intent(this, SystemCheckActivity::class.java))

    /**
     * Open one of the console's sections.
     *
     * The phone and the browser show the same pages; this is the phone's way in
     * to one of them. Unconfigured it opens the phone's own settings instead,
     * because that is where the answer is and because the alternative was
     * circular: with PHONE moved into the console frame's tab strip, MANAGE is
     * the only way to that strip, and telling somebody who just tapped MANAGE
     * to go and find PHONE — which lives behind MANAGE — is a loop. Nothing is
     * lost by going straight there: the sentence it used to print named the one
     * screen this now opens.
     */
    private fun openConsole(tab: ConsoleTab) {
        if (!config.isConfigured) {
            openSettings()
            return
        }
        startActivity(ManagementActivity.intent(this, tab))
    }

    /**
     * Back to tap-to-speak.
     *
     * The orb's caption and the pill are two different views a few dp apart in
     * the same accent monospace, and they used to carry the same string — so
     * the home screen showed "TAP TO SPEAK" twice, the second one being canvas
     * text with no click listener and no accessibility node. The caption is the
     * one that gives: it is a status readout (LISTENING / PROCESSING /
     * RESPONDING / ERROR everywhere else), and the pill is the only affordance
     * that is real, tappable and visible to TalkBack.
     */
    private fun showIdle() {
        orbView.setAmplitude(0f)
        orbView.setMode(JarvisOrbView.Mode.IDLE)
        orbView.setStateLabel(IDLE_CAPTION)
        brandBar.setStatus(IDLE_CAPTION, ConsoleFrame.Tone.NEUTRAL)
        refreshMuteButton()
        if (!config.isConfigured) {
            responseView.text = "Tap MANAGE to point me at your Jarvis server."
        }
    }

    // --- JarvisConversation.Ui (main thread) --------------------------------

    override fun onMode(mode: JarvisOrbView.Mode, label: String) {
        orbView.setMode(mode)
        orbView.setStateLabel(label)
        brandBar.setStatus(
            label,
            if (mode == JarvisOrbView.Mode.IDLE) ConsoleFrame.Tone.NEUTRAL else ConsoleFrame.Tone.LIVE,
        )
        // Reaching LISTENING is the proof that the whole chain works — socket,
        // token, pipeline, microphone — so it is what clears the backoff.
        // Clearing on start instead would reset it on the very failure it
        // exists to slow down.
        if (label == "LISTENING") restartBackoffMs = 0
        // The pill stays a mute. It used to mirror the state word, which made
        // the only off switch read as a status line and change what a tap did
        // depending on when it landed.
        refreshMuteButton()
    }

    override fun onAmplitude(level: Float) = orbView.setAmplitude(level)

    override fun onTranscript(text: String) {
        transcriptView.text = text
    }

    override fun onResponse(text: String) {
        responseView.text = text
    }

    override fun onError(message: String) {
        responseView.text = message
        orbView.setStateLabel("ERROR")
        orbView.setMode(JarvisOrbView.Mode.ERROR)
        brandBar.setStatus("ERROR", ConsoleFrame.Tone.WARN)
    }

    override fun onTools(run: ToolRun) = toolActivityView.render(run)
    override fun onActivity(rows: ActivityRows) = activityStrip.render(rows)
    override fun onKnowledge(nodes: List<KnowledgeGraph.Node>, edges: List<KnowledgeGraph.Edge>) = knowledgeGraphView.render(nodes, edges)
    override fun onKnowledgePulse(ids: List<String>) = knowledgeGraphView.pulse(ids)
    override fun onWork() = orbView.work()
    override fun onLooking(looking: Boolean) {
        orbView.looking = looking
    }

    /**
     * A conversation ended. On a screen with no talk button, that is not a
     * resting state — it is a gap in the one thing this screen does.
     *
     * A continuous conversation only ends badly: the socket dropped, the
     * server refused, the pipeline errored. So this always re-opens, and the
     * backoff is what keeps "always" from meaning "as fast as the failure
     * repeats". It is cleared the moment a conversation gets as far as
     * LISTENING (see [onMode]), so a single flaky reconnect costs a second and
     * a genuinely unreachable server settles at one attempt every 30.
     */
    override fun onIdle() {
        showIdle()
        convo = null
        if (!inForeground || config.micMuted || !config.isConfigured) return
        restartBackoffMs = if (restartBackoffMs <= 0) {
            RESTART_BACKOFF_MS
        } else {
            minOf(restartBackoffMs * 2, RESTART_BACKOFF_MAX_MS)
        }
        handler.removeCallbacks(restart)
        handler.postDelayed(restart, restartBackoffMs)
    }

    override fun onDestroy() {
        convo?.stop()
        convo = null
        // Detaching the overlay cancels its clock and drops its reference to
        // the orb; skipping first makes sure the orb is left settled rather
        // than frozen mid-ignition if this Activity dies during the boot.
        //
        // The completion callback goes first, though. It exists to hand the
        // home screen back — fade the controls up, re-probe the checklist — and
        // running that against an Activity that is being destroyed is work for
        // a screen nobody will see again.
        boot?.let { it.onComplete = null; it.skip() }
        boot = null
        super.onDestroy()
    }

    companion object {
        /**
         * How often the hands-free screen checks that it can still hear.
         *
         * Not an inactivity timeout — a continuous conversation has none, since
         * silence is what a room full of nobody talking sounds like. This is
         * only the period at which a DEAD microphone is looked for; see
         * JarvisConversation.inactivity. Long enough to be free, short enough
         * that a phone which cannot hear says so while the user is still
         * holding it.
         */
        private const val DEAF_CHECK_MS = 20_000L

        /** First backoff after a conversation that failed, and its ceiling. */
        private const val RESTART_BACKOFF_MS = 1_500L
        private const val RESTART_BACKOFF_MAX_MS = 30_000L

        private const val REQ_MIC = 4712
        private const val REQ_NOTIFICATIONS = 4713
        private const val REQ_MIC_FOR_WAKE = 4714

        /**
         * Spelled out rather than `Manifest.permission.POST_NOTIFICATIONS`,
         * which is API 33+. The string is stable, and writing it means no
         * version guard at the constant.
         */
        private const val POST_NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS"

        /**
         * The orb's caption while idle. A state word, deliberately NOT the
         * talk button's label — see [showIdle].
         */
        internal const val IDLE_CAPTION = "STANDBY"

        /**
         * Backstop for a splash-exit listener that never fires. Long enough
         * that the normal handoff always wins, short enough that a broken ROM
         * costs a beat rather than a black screen.
         */
        private const val SPLASH_FALLBACK_MS = 700L
    }
}
