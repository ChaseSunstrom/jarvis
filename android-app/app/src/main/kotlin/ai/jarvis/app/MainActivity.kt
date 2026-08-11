package ai.jarvis.app

import ai.jarvis.app.assist.JarvisConversation
import ai.jarvis.app.assist.ToolActivityView
import ai.jarvis.app.assist.ToolRun
import ai.jarvis.app.assist.WakeStartPolicy
import ai.jarvis.app.assist.WakeWordService
import ai.jarvis.app.automation.JarvisAutomationService
import ai.jarvis.app.compat.GrapheneCompat
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisBootAnimation
import ai.jarvis.app.ui.ConsoleTab
import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.SystemCheckActivity
import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView

/**
 * The Jarvis home — the face of the app. Opening it lands on the orb, not a
 * dashboard: tap to talk, watch the transcript and the reply, and get to the
 * other surfaces from the bottom row.
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
    private lateinit var talkButton: Button
    private lateinit var listenButton: Button
    private lateinit var listenReason: TextView
    private lateinit var config: JarvisConfig

    /** Everything that is not the orb: transcript, reply, controls, nav. */
    private lateinit var homeControls: LinearLayout
    private lateinit var root: FrameLayout
    private lateinit var bannerSlot: FrameLayout

    private var convo: JarvisConversation? = null
    private var boot: JarvisBootAnimation? = null

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
            onHomeAlpha = { a -> homeControls.alpha = a }
            onComplete = {
                boot = null
                homeControls.alpha = 1f
                showIdle()
                refreshStatusBanner()
            }
        }
        boot = animation
        homeControls.alpha = 0f
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

        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            val m = JarvisUi.dp(this@MainActivity, 24)
            setPadding(m, 0, m, JarvisUi.dp(this@MainActivity, 36))
        }
        homeControls = col

        bannerSlot = FrameLayout(this)
        transcriptView = JarvisUi.transcriptView(this)
        responseView = JarvisUi.responseView(this)
        toolActivityView = ToolActivityView(this)
        talkButton = JarvisUi.pill(this, "TAP TO SPEAK") { toggleTalk() }

        // The always-on listener's actual state, on the screen the user opens.
        //
        // Reported three times as "I have to select start listening in the app
        // before it works". There was nothing here to select: the only control
        // was a switch in Settings behind a SAVE button, and nothing anywhere
        // said whether the listener was running. So the app both LOOKED
        // stateless and gave no way to change the state — and every diagnosis
        // of it was guesswork about somebody else's phone.
        listenButton = JarvisUi.ghost(this, "…") { toggleListening() }
        listenReason = TextView(this).apply {
            setTextColor(JarvisUi.DIM)
            textSize = 11f
            gravity = Gravity.CENTER
            setPadding(0, JarvisUi.dp(this@MainActivity, 4), 0, 0)
        }

        // The console's own nav, on the phone, in the console's order.
        //
        // It used to be MANAGE / AUTOMATIONS / SETTINGS, and only one of those
        // three had a counterpart in the browser: MANAGE opened the console's
        // front door with no way on to its other four sections, SETTINGS opened
        // a native screen about this phone, and AUTOMATIONS opened a native
        // screen listing the tasks THIS PHONE runs by itself — a different
        // thing from the house's automations that happens to share a word.
        // Which is the whole of "it feels weird that it's kind of similar but
        // not really". See ConsoleTab.
        val nav = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            for (tab in ConsoleTab.entries) {
                addView(JarvisUi.ghost(this@MainActivity, tab.label) { openConsole(tab) })
                addView(navSpacer())
            }
            // The mobile half, and deliberately not called Settings: a button
            // named Settings beside a tab named SETTINGS is how the phone's own
            // configuration and the house's got confused to begin with.
            addView(
                JarvisUi.ghost(this@MainActivity, ConsoleTab.PHONE_LABEL) { openSettings() }
            )
        }
        // Six monospace labels do not fit a phone's width, and a nav that wraps
        // into two ragged lines is the thing this replaced.
        val navScroll = HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            setPadding(0, JarvisUi.dp(this@MainActivity, 18), 0, 0)
            clipToPadding = false
            // Centred when the six labels fit, scrollable when they do not.
            isFillViewport = true
            // FrameLayout params, not LinearLayout's: HorizontalScrollView IS a
            // FrameLayout, and FrameLayout.onMeasure casts its child's
            // LayoutParams — the wrong type is a ClassCastException on the
            // first measure pass rather than a layout that looks a bit off.
            addView(
                nav,
                FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            )
        }

        col.addView(
            bannerSlot,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = JarvisUi.dp(this@MainActivity, 14) }
        )
        col.addView(
            toolActivityView,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = JarvisUi.dp(this@MainActivity, 10) }
        )
        col.addView(transcriptView)
        col.addView(responseView)
        col.addView(
            talkButton,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = JarvisUi.dp(this@MainActivity, 22) }
        )
        col.addView(
            listenButton,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = JarvisUi.dp(this@MainActivity, 10) }
        )
        col.addView(listenReason, fullWidthParams())
        col.addView(navScroll)

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

    private fun navSpacer(): View = View(this).apply {
        layoutParams = LinearLayout.LayoutParams(JarvisUi.dp(this@MainActivity, 10), 1)
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

    private fun toggleTalk() {
        // A tap during the power-on means "skip it", not "start talking".
        boot?.let { it.skip(); return }

        val c = convo
        if (c != null && c.isRunning) {
            c.stop(); showIdle(); return
        }
        if (!config.isConfigured) {
            openSettings(); return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC); return
        }
        transcriptView.text = ""
        responseView.text = ""
        toolActivityView.hide()
        talkButton.text = "LISTENING… (TAP TO STOP)"
        convo = JarvisConversation(this, config, this, inactivityMs = 12000L).also { it.start() }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC &&
            grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED
        ) toggleTalk()
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
     * to one of them. Unconfigured, it says so rather than opening a WebView
     * onto nothing — the console is not something this app can render itself.
     */
    private fun openConsole(tab: ConsoleTab) {
        if (!config.isConfigured) {
            responseView.text = "Set the server URL and token under ${ConsoleTab.PHONE_LABEL} first."
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
        talkButton.text = "TAP TO SPEAK"
        if (!config.isConfigured) {
            responseView.text = "Tap SETTINGS to point me at your Jarvis server."
        }
    }

    // --- JarvisConversation.Ui (main thread) --------------------------------

    override fun onMode(mode: JarvisOrbView.Mode, label: String) {
        orbView.setMode(mode)
        orbView.setStateLabel(label)
        talkButton.text = when (label) {
            "LISTENING" -> "LISTENING… (TAP TO STOP)"
            else -> "$label… (TAP TO STOP)"
        }
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
    }

    override fun onTools(run: ToolRun) = toolActivityView.render(run)

    override fun onIdle() = showIdle()

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
        private const val IDLE_CAPTION = "STANDBY"

        /**
         * Backstop for a splash-exit listener that never fires. Long enough
         * that the normal handoff always wins, short enough that a broken ROM
         * costs a beat rather than a black screen.
         */
        private const val SPLASH_FALLBACK_MS = 700L
    }
}
