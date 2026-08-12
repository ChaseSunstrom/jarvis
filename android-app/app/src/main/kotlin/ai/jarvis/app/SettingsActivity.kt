package ai.jarvis.app

import ai.jarvis.app.assist.LocalTranscriber
import ai.jarvis.app.assist.ModelStore
import ai.jarvis.app.assist.OnDeviceWakeWord
import ai.jarvis.app.assist.WakeWordService
import ai.jarvis.app.automation.JarvisAutomationService
import ai.jarvis.app.automation.actions.ActionEnv
import ai.jarvis.app.channel.ChannelConfig
import ai.jarvis.app.channel.DeviceChannelHost
import ai.jarvis.app.compat.GrapheneCompat
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.config.PairingClaim
import ai.jarvis.app.config.PairingPayload
import ai.jarvis.app.config.ServerUrl
import ai.jarvis.app.update.UpdateChecker
import ai.jarvis.app.ui.ConsoleFrame
import ai.jarvis.app.ui.JarvisScreens
import ai.jarvis.app.ui.JarvisUi
import android.annotation.SuppressLint
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.Gravity
import android.view.ViewGroup
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast

/**
 * Connection and behaviour settings.
 *
 * Everything here is programmatic for the same reason the rest of the app is:
 * one visual language, no layout XML to drift. The screen is deliberately
 * plain-spoken about what it is asking for — the token it stores is the key to
 * the whole house.
 */
class SettingsActivity : Activity() {

    private lateinit var config: JarvisConfig

    private lateinit var urlField: EditText
    private lateinit var tokenField: EditText
    private lateinit var pipelineField: EditText
    private lateinit var deviceNameField: EditText

    private lateinit var overlayStatus: TextView
    private lateinit var listenStatus: TextView
    private lateinit var modelStatus: TextView
    private lateinit var wakeOnDevice: Switch
    private lateinit var sttOnDevice: Switch
    private lateinit var sttStatus: TextView
    private lateinit var updateStatus: TextView
    private lateinit var prereleaseUpdates: Switch
    private lateinit var wakeEnabled: Switch
    private lateinit var wakeInCar: Switch
    private lateinit var wakeAtHome: Switch
    private lateinit var headsetMode: Switch
    private lateinit var headsetWarmLink: Switch
    private lateinit var headsetButton: Switch
    /** How many required grants are missing, refreshed on resume. */
    private lateinit var permissionStatus: TextView

    /**
     * Waking hours, as indices rather than text.
     *
     * They used to be two `EditText`s that accepted "25", "nine" and the empty
     * string for a value with twenty-four possibilities, each needing its own
     * parse, fallback and explanatory hint. Start is 0..23; end is 1..24, where
     * 24 means midnight — see `WakeWordGate`, which treats the window as
     * half-open.
     */
    private var wakingStart = 0
    private var wakingEnd = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        config = JarvisConfig(this)
        setContentView(buildUi())
    }

    private fun buildUi(): ViewGroup {
        val ctx = this
        val root = FrameLayout(ctx).apply { setBackgroundColor(JarvisUi.BG) }
        val col = JarvisUi.column(ctx, padDp = 20)

        col.addView(JarvisUi.title(ctx, "JARVIS"))
        col.addView(
            TextView(ctx).apply {
                text = "SETTINGS"
                setTextColor(JarvisUi.DIM)
                textSize = 11f
                letterSpacing = 0.3f
                gravity = Gravity.CENTER
            }
        )

        // --- server ---------------------------------------------------------

        col.addView(JarvisUi.label(ctx, "Server URL"))
        urlField = JarvisUi.field(ctx, "http://192.168.2.10:8123", config.serverUrl)
        col.addView(urlField, matchWidth())
        col.addView(
            JarvisUi.hint(
                ctx,
                "The address you reach jarvis-core on over LAN or WireGuard. Plain http is " +
                    "accepted only for private addresses, and only for the hosts listed in " +
                    "res/xml/network_security_config.xml."
            )
        )

        col.addView(JarvisUi.label(ctx, "Access token"))
        tokenField = JarvisUi.field(ctx, "long-lived access token", config.token, secret = true)
        col.addView(tokenField, matchWidth())
        col.addView(
            row(
                JarvisUi.ghost(ctx, "PASTE") { pasteToken() },
                JarvisUi.ghost(ctx, "SCAN QR") { scanToken() },
            )
        )
        col.addView(
            JarvisUi.hint(
                ctx,
                "Create it in the Jarvis management UI. It is stored on this device only, is " +
                    "excluded from backups, and is never sent anywhere but your server."
            )
        )

        col.addView(JarvisUi.label(ctx, "Pipeline name"))
        pipelineField = JarvisUi.field(ctx, JarvisConfig.DEFAULT_PIPELINE, config.pipeline)
        col.addView(pipelineField, matchWidth())

        col.addView(JarvisUi.label(ctx, "Device name"))
        deviceNameField = JarvisUi.field(ctx, "This phone", config.deviceName)
        col.addView(deviceNameField, matchWidth())
        col.addView(
            JarvisUi.hint(ctx, "Shown on the server when this device registers. Device id: ${config.deviceId}")
        )

        // --- voice ----------------------------------------------------------

        col.addView(JarvisUi.label(ctx, "Voice"))
        wakeEnabled = switchRow(ctx, "Listen for \"Hey Jarvis\"", config.wakeWordEnabled)
        col.addView(wakeEnabled, matchWidth())
        col.addView(
            JarvisUi.hint(
                ctx,
                "Android gives third-party apps no low-power hotword path, so always-on " +
                    "detection means a genuinely open mic and real battery cost."
            )
        )

        // Whether listening can come back on its own after a restart, which is
        // a different question from whether it is switched on and has nowhere
        // else to be answered. Android will not let a microphone service start
        // from the background, so without one of these two grants the switch
        // above is only true until the next reboot — and the failure is silent,
        // which is exactly how it was found: by the phone quietly not
        // listening until the app was opened again.
        listenStatus = TextView(ctx).apply { textSize = 12f }
        col.addView(listenStatus)
        col.addView(
            row(
                JarvisUi.ghost(ctx, "ALLOW BACKGROUND") { requestBackgroundStart() },
                JarvisUi.ghost(ctx, "DISPLAY OVER APPS") { openOverlaySetting() },
            )
        )
        overlayStatus = TextView(ctx).apply {
            setTextColor(JarvisUi.DIM)
            textSize = 12f
        }
        col.addView(overlayStatus)

        // --- on this phone ----------------------------------------------------

        col.addView(JarvisUi.spacer(ctx, 12))
        col.addView(JarvisUi.label(ctx, "On this phone"))
        col.addView(
            JarvisUi.hint(
                ctx,
                "Off, the microphone streams continuously to your server, which does the " +
                    "detecting — everything the room says, all the time. On, the phone " +
                    "decides for itself and nothing is sent until you have said the name. " +
                    "The models are about 3.6 MB and come from YOUR server, not from the " +
                    "internet: Jarvis mirrors them so the phone never has to talk to anyone " +
                    "else."
            )
        )
        wakeOnDevice = switchRow(ctx, "Detect \u201CHey Jarvis\u201D on this phone", config.wakeWordOnDevice)
        // Every one of these switches has a line of status under it that says
        // what is ACTUALLY happening, and a status line that only refreshes on
        // save is a status line that contradicts the switch above it for as
        // long as the user is looking at both.
        wakeOnDevice.setOnCheckedChangeListener { _, _ ->
            if (::modelStatus.isInitialized) refreshModelStatus()
        }
        col.addView(wakeOnDevice, matchWidth())
        modelStatus = TextView(ctx).apply { textSize = 12f }
        col.addView(modelStatus)
        col.addView(
            row(
                JarvisUi.ghost(ctx, "DOWNLOAD MODELS") { downloadModels() },
                JarvisUi.ghost(ctx, "DELETE MODELS") { deleteModels() },
            )
        )

        // Named for the thing it is, because the two are constantly confused:
        // the models above are the WAKE WORD's, and they have nothing to do
        // with transcription. "I have the models downloaded, why isn't it
        // transcribing on my phone" is that confusion, and it is the app's
        // fault for putting one switch under the other with no label between.
        col.addView(JarvisUi.spacer(ctx, 12))
        col.addView(JarvisUi.label(ctx, "Speech to text"))
        col.addView(
            JarvisUi.hint(
                ctx,
                "Separate from the models above. Transcription uses Android's own " +
                    "offline recogniser, which is part of the system rather than " +
                    "something Jarvis can download \u2014 if this phone does not have one, " +
                    "the line below says so."
            )
        )
        sttOnDevice = switchRow(ctx, "Transcribe on this phone", config.sttOnDevice)
        sttOnDevice.setOnCheckedChangeListener { _, _ ->
            if (::sttStatus.isInitialized) refreshSttStatus()
        }
        col.addView(sttOnDevice, matchWidth())
        sttStatus = TextView(ctx).apply { textSize = 12f }
        col.addView(sttStatus)

        // --- in the car -------------------------------------------------------
        //
        // Reported as "why is Jarvis not showing up in Android Auto". The car
        // module is real and correct; the reason it is invisible is that the
        // build is sideloaded, and Android Auto's unknown-sources developer
        // option explicitly does not cover Car App Library apps. Nothing in
        // this app can change that, so the only useful thing it can do is stop
        // the user concluding the feature is broken.
        col.addView(JarvisUi.spacer(ctx, 12))
        col.addView(JarvisUi.label(ctx, "In the car"))
        col.addView(
            JarvisUi.hint(
                ctx,
                "Jarvis has an Android Auto screen — the orb, what you said and the " +
                    "reply — but a SIDELOADED build never appears in the car. Android " +
                    "Auto's \"unknown sources\" developer setting covers media, messaging " +
                    "and parked apps, and Google documents that it does not cover apps " +
                    "built with the Android for Cars App Library. This one is.\n\n" +
                    "Two ways to see it: install from Google Play (an internal-testing " +
                    "track with one tester is enough — installing from Play is what makes " +
                    "the app a trusted source), or run the Desktop Head Unit on a computer " +
                    "and connect this phone to it, which is the supported way to develop " +
                    "against it. docs/android-auto.md has both.\n\n" +
                    "Voice in the car is separate and is not affected: \"Hey Jarvis\" runs " +
                    "on this phone while Android Auto is connected, and the reply plays " +
                    "through the car's speakers over Bluetooth."
            )
        )

        // --- whose voice ------------------------------------------------------
        col.addView(JarvisUi.spacer(ctx, 12))
        col.addView(JarvisUi.label(ctx, "Whose voice"))
        col.addView(
            JarvisUi.hint(
                ctx,
                "Jarvis can be told to answer only you. Enrolling teaches it what you " +
                    "sound like; whether it refuses anyone else is a setting on the server, " +
                    "so that turning on the thing which can refuse you is never a switch " +
                    "you hit by accident here."
            )
        )
        col.addView(
            JarvisUi.ghost(ctx, "TEACH JARVIS MY VOICE") {
                startActivity(Intent(this, VoiceIdentityActivity::class.java))
            },
            matchWidth()
        )
        col.addView(
            JarvisUi.hint(
                ctx,
                "Transcribing on this phone and \"only my voice\" cannot both be in " +
                    "force: the check runs on your server, on the sound, and a turn this " +
                    "phone transcribes sends words instead. Nothing is left to you — " +
                    "while the gate is enforcing, on-device transcription suspends itself " +
                    "and the line above says so. It cannot simply move here either: " +
                    "Android's offline recogniser owns the microphone and hands this app " +
                    "partial text and a level, never the audio."
            )
        )

        // --- when to listen ---------------------------------------------------
        //
        // Kept together and labelled for what they are. `WakeWordGate` needs to
        // know whether the phone is at home, and nothing on this device produces
        // that signal — there is no home-presence source anywhere in the app.
        // Enforcing the gate without one would mean "not at home" always, which
        // would silence the wake word everywhere except a car. So these are
        // stored and not yet applied, and a settings screen that implied
        // otherwise would be lying. One admission, in one place, instead of the
        // two paragraphs this used to spend saying it.

        col.addView(JarvisUi.spacer(ctx, 12))
        col.addView(JarvisUi.label(ctx, "When to listen — saved, not yet in effect"))
        wakeInCar = switchRow(ctx, "While car Bluetooth is connected", config.wakeInCar)
        col.addView(wakeInCar, matchWidth())
        wakeAtHome = switchRow(ctx, "While at home, during waking hours", config.wakeAtHome)
        col.addView(wakeAtHome, matchWidth())
        col.addView(hourRow(ctx), matchWidth())
        col.addView(
            JarvisUi.hint(
                ctx,
                "Jarvis has no home-presence signal on this device yet, so these are " +
                    "remembered but not applied — the switch above is what decides. A window " +
                    "that wraps midnight (22 to 6) is fine."
            )
        )

        // --- headset / earpiece -----------------------------------------------
        //
        // These three settings had getters, defaults, a whole page of
        // documentation in docs/earpiece.md, pure routing logic checked over
        // every combination — and no way to turn any of them on. `headsetMode`
        // defaults to false and nothing in the app wrote it, so the earpiece
        // feature was unreachable in exactly the way `MediaButtonGate` was.
        //
        // Off by default is deliberate and stays: plugging in a headset must
        // never silently move the microphone off the phone.

        col.addView(JarvisUi.spacer(ctx, 16))
        col.addView(JarvisUi.label(ctx, "Headset"))
        headsetMode = switchRow(ctx, "Capture through a connected headset", config.headsetMode)
        col.addView(headsetMode, matchWidth())
        headsetButton = switchRow(ctx, "Let its button summon Jarvis", config.headsetButton)
        col.addView(headsetButton, matchWidth())
        headsetWarmLink = switchRow(ctx, "Keep listening after a reply", config.warmLink)
        col.addView(headsetWarmLink, matchWidth())
        col.addView(
            JarvisUi.hint(
                ctx,
                "Only when the headset has a microphone of its own. Jarvis then captures " +
                    "through the phone's call path, where the hardware echo canceller stops " +
                    "it hearing its own reply two centimetres away — at some cost to " +
                    "transcription accuracy, which is why it is not the default. Keeping " +
                    "the mic open after a reply needs that canceller, so it does nothing on " +
                    "a route without one. The button never answers a confirmation prompt."
            )
        )

        // --- permissions ------------------------------------------------------
        //
        // One button, not a grid of ten. This screen used to carry its own row
        // of raw Settings shortcuts — ASSISTANT, ACCESSIBILITY, NOTIFICATIONS,
        // OVERLAY, FULL SCREEN, NOTIFICATION SETTINGS, BATTERY, APP INFO — with
        // no indication of which were granted, two of them opening the same
        // screen as buttons already above, and two more with almost the same
        // name and completely different meanings. SystemCheckActivity lists
        // every one of them WITH its state, whether it is required, and what
        // breaks without it. Sending people there is strictly better than a
        // worse copy of it.

        col.addView(JarvisUi.spacer(ctx, 12))
        col.addView(JarvisUi.label(ctx, "Permissions"))
        permissionStatus = TextView(ctx).apply { textSize = 12f }
        col.addView(permissionStatus)
        col.addView(
            row(
                JarvisUi.ghost(ctx, "SYSTEM CHECK") {
                    startActivity(Intent(this, ai.jarvis.app.ui.SystemCheckActivity::class.java))
                },
                JarvisUi.ghost(ctx, "APP INFO") { openAppInfo() },
            )
        )
        col.addView(
            JarvisUi.hint(
                ctx,
                "System check lists every permission and special access Jarvis can use, " +
                    "whether it is granted, and what stops working without it. None of them " +
                    "changes what tier an action is — they only decide whether it is possible."
            )
        )

        // --- other screens --------------------------------------------------

        col.addView(JarvisUi.spacer(ctx, 12))
        col.addView(JarvisUi.label(ctx, "More"))
        col.addView(
            row(
                // NOT "automations". The console has a page by that name and it
                // is the HOUSE's; this one lists the tasks this phone runs by
                // itself, which is a different thing that happened to share a
                // word — and sharing it made the app feel like a slightly wrong
                // copy of the console rather than the other half of it.
                JarvisUi.ghost(ctx, "PHONE TASKS") {
                    JarvisScreens.open(this, JarvisScreens.AUTOMATIONS, "Phone tasks")
                },
                JarvisUi.ghost(ctx, "AUDIT LOG") {
                    JarvisScreens.open(this, JarvisScreens.AUDIT_LOG, "The audit log")
                },
            )
        )
        col.addView(
            JarvisUi.hint(
                ctx,
                "Phone tasks are what THIS device does on its own \u2014 a geofence, a media " +
                    "button, a rule pushed to it. The house's automations live in the " +
                    "console's AUTOMATIONS tab, on this phone and in a browser alike."
            )
        )
        col.addView(
            row(
                JarvisUi.ghost(ctx, "CRASH LOGS") {
                    startActivity(Intent(this, ai.jarvis.app.ui.CrashLogActivity::class.java))
                },
            )
        )
        col.addView(
            JarvisUi.hint(
                ctx,
                "The audit log records every action this device actually executed, with its " +
                    "tier and how it was authorised. Both it and the crash logs are local and yours."
            )
        )

        // --- updates ----------------------------------------------------------

        col.addView(JarvisUi.spacer(ctx, 16))
        col.addView(JarvisUi.label(ctx, "Updates"))
        updateStatus = TextView(ctx).apply {
            text = "Version ${appVersionName()} (build ${appVersionCode()})"
            setTextColor(JarvisUi.DIM)
            textSize = 12f
        }
        col.addView(updateStatus)
        prereleaseUpdates = switchRow(ctx, "Include test builds", config.allowPrereleaseUpdates)
        col.addView(prereleaseUpdates)
        col.addView(
            row(
                JarvisUi.ghost(ctx, "CHECK FOR UPDATES") { checkForUpdates() },
                JarvisUi.ghost(ctx, "RELEASES") { openReleasesPage() },
            )
        )
        // The grant without which none of the above can finish. Declared in the
        // manifest since the app was written, and a per-app user decision since
        // Android 8 — so it is another one that looked handled and was not.
        col.addView(
            JarvisUi.ghost(ctx, "INSTALL PERMISSION") { openInstallPermission() },
            matchWidth()
        )

        // --- save -----------------------------------------------------------

        col.addView(JarvisUi.spacer(ctx, 16))
        col.addView(
            JarvisUi.pill(ctx, "SAVE") { save() },
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )
        col.addView(JarvisUi.spacer(ctx, 24))

        val scroll = ScrollView(ctx).apply {
            isFillViewport = true
            addView(
                col,
                ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            )
        }

        // The console's nav, above this screen exactly as it sits above the
        // console's own sections — this is the "same web view look" half of
        // deduplicating the two. What is BELOW it cannot be a web page: asking
        // for RECORD_AUDIO, taking a battery exemption and downloading a wake
        // word model are things a page in a WebView cannot do. So the frame is
        // shared and the content is native, and PHONE stops being a screen off
        // to one side that you reach from somewhere else.
        val framed = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        framed.addView(
            ConsoleFrame.tabBar(this, current = null, onPhone = true) { tab ->
                startActivity(ManagementActivity.intent(this, tab))
            },
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )
        framed.addView(
            scroll,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f)
        )
        root.addView(
            framed,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        return root
    }

    /**
     * "Waking hours  [08]  to  [22]", both pickers.
     *
     * The end offers 24 and the start does not, because the window is
     * half-open: `WakeWordGate` includes the start hour and excludes the end,
     * so an end of 24 is midnight and an end of 0 would be an empty window on
     * every day that does not wrap.
     */
    private fun hourRow(ctx: Context): LinearLayout {
        wakingStart = config.wakingHourStart.coerceIn(0, 23)
        wakingEnd = config.wakingHourEnd.coerceIn(1, 24)
        val starts = (0..23).map { "%02d:00".format(it) }
        val ends = (1..24).map { "%02d:00".format(it % 24) }
        return LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(
                TextView(ctx).apply {
                    text = "Waking hours"
                    setTextColor(JarvisUi.DIM)
                    textSize = 14f
                },
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            )
            addView(
                JarvisUi.chooser(ctx, "Start of waking hours", starts, wakingStart) {
                    wakingStart = it
                }
            )
            addView(
                TextView(ctx).apply {
                    text = " to "
                    setTextColor(JarvisUi.FAINT)
                    textSize = 14f
                }
            )
            addView(
                JarvisUi.chooser(ctx, "End of waking hours", ends, wakingEnd - 1) {
                    wakingEnd = it + 1
                }
            )
        }
    }

    // --- persistence --------------------------------------------------------

    private fun save() {
        val check = ServerUrl.check(urlField.text.toString())
        if (!check.isValid) {
            toast(check.error ?: "Invalid server URL")
            return
        }
        val token = tokenField.text.toString().trim()
        if (token.isEmpty()) {
            toast("An access token is required")
            return
        }

        config.serverUrl = check.normalized
        config.token = token
        config.pipeline = pipelineField.text.toString()
        config.allowPrereleaseUpdates = prereleaseUpdates.isChecked
        config.deviceName = deviceNameField.text.toString()

        config.wakeWordEnabled = wakeEnabled.isChecked
        config.wakeWordOnDevice = wakeOnDevice.isChecked
        config.sttOnDevice = sttOnDevice.isChecked
        config.wakeInCar = wakeInCar.isChecked
        config.wakeAtHome = wakeAtHome.isChecked
        config.wakingHourStart = wakingStart
        config.wakingHourEnd = wakingEnd

        // Order matters by one line: `warmLink` and `headsetButton` read
        // `headsetMode` in their own getters, so writing the mode first is what
        // makes the two below mean what the screen showed.
        config.headsetMode = headsetMode.isChecked
        config.headsetButton = headsetButton.isChecked
        config.warmLink = headsetWarmLink.isChecked

        // Both of these run once at startup and then never again, so without
        // this the app keeps the values it read before the user typed anything:
        // ActionEnv would hold the OLD jarvis-core host (the one exemption
        // `http_request` has from its SSRF guard) and the command channel would
        // sit out its backoff before noticing the new URL.
        runCatching { ActionEnv.refreshFromConfig(applicationContext) }
        runCatching { DeviceChannelHost.configChanged() }
        // A phone that has just been given a server for the first time should
        // not have to wait for a reboot to connect to it.
        runCatching { JarvisAutomationService.ensureRunning(this, "settings-saved") }
        // Saving is where wakeWordEnabled changes, so it is also where the
        // listener has to start or stop. ensureRunning checks the setting
        // itself; the stop is explicit because nothing else would issue it.
        runCatching {
            if (config.wakeWordEnabled) {
                WakeWordService.ensureRunning(this, fromForeground = true)
                // Said at the moment it becomes relevant, rather than left on a
                // status line further up the screen the user has already
                // scrolled past. Without one of these two, listening works
                // until the next reboot and then quietly does not.
                if (!isExemptFromBatteryOptimisation() && !Settings.canDrawOverlays(this)) {
                    toast("Listening will stop at the next restart — see ALLOW BACKGROUND above")
                }
            } else {
                WakeWordService.cancelHeartbeat(this)
                WakeWordService.clearAttention(this)
                stopService(Intent(this, WakeWordService::class.java))
            }
        }

        check.warning?.let { toast(it) }
        toast("Saved")
        finish()
    }

    // --- token entry --------------------------------------------------------

    private fun pasteToken() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        val text = clipboard?.primaryClip
            ?.takeIf { it.itemCount > 0 }
            ?.getItemAt(0)
            ?.coerceToText(this)
            ?.toString()
            ?.trim()
        if (text.isNullOrEmpty()) {
            toast("Clipboard is empty")
            return
        }
        tokenField.setText(text)
        toast("Pasted ${text.length} characters")
    }

    /**
     * Hands off to whatever barcode scanner the user installed. Jarvis bundles
     * no scanner: every offline decoder worth using is a large dependency, and
     * the Google ones are exactly what a degoogled phone is avoiding. Binary Eye
     * and QR Scanner (both F-Droid) answer this intent.
     */
    private fun scanToken() {
        val intent = Intent(ZXING_SCAN)
            .putExtra("SCAN_MODE", "QR_CODE_MODE")
            .putExtra("SAVE_HISTORY", false)
        try {
            startActivityForResult(intent, REQ_SCAN)
        } catch (e: ActivityNotFoundException) {
            toast("No QR scanner installed. Install Binary Eye from F-Droid, or use PASTE.")
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_SCAN || resultCode != RESULT_OK) return
        val scanned = data?.getStringExtra("SCAN_RESULT")?.trim()
        if (scanned.isNullOrEmpty()) {
            toast("Nothing scanned")
            return
        }

        // Three outcomes, and the middle one is the reason this is not just
        // `tokenField.setText(scanned)`.
        when (val parsed = PairingPayload.parse(scanned)) {
            is PairingPayload.Result.Ok -> pair(parsed.payload)
            // Recognisably one of ours and not acceptable — a stale version, an
            // address this app may not dial, a malformed code. This must never
            // fall through to the bare-token path: doing so would put a refused
            // payload's text in the token field and call it success.
            is PairingPayload.Result.Refused -> toast(parsed.message)
            // Not addressed to us at all, so it is somebody's hand-made QR with
            // a token in it — the way this worked before pairing existed, and
            // still the fallback for a server too old to pair.
            is PairingPayload.Result.NotAPayload -> {
                tokenField.setText(scanned)
                toast("Scanned ${scanned.length} characters")
            }
        }
    }

    /**
     * Exchange a scanned pairing code for a token, off the main thread.
     *
     * Fills BOTH fields on success: the address travelled in the QR alongside
     * the code, which is the point — the whole reason typing a token is the
     * worst moment of setup is that it comes with an address to type as well.
     */
    private fun pair(payload: PairingPayload) {
        toast("Pairing…")
        Thread {
            val result = PairingClaim.claim(
                payload,
                deviceName = deviceNameField.text.toString().trim()
                    .ifEmpty { config.deviceName },
                // Read through ChannelConfig, which is the one place that
                // knows where the user's own cleartext acknowledgements live.
                // The QR must not be able to add to that list — only a person
                // typing on this device can.
                acknowledgedCleartextHosts =
                    ChannelConfig.from(this, appVersionName()).acknowledgedCleartextHosts,
            )
            runOnUiThread {
                when (result) {
                    is PairingClaim.Result.Ok -> {
                        urlField.setText(result.url)
                        tokenField.setText(result.token)
                        toast("Paired as ${result.name}. Tap SAVE to finish.")
                    }
                    is PairingClaim.Result.Failed -> toast(result.message)
                }
            }
        }.start()
    }

    /**
     * Re-read on resume: granting the permission happens in another app, so the
     * only moment this can be correct is when we come back from it.
     */
    override fun onResume() {
        super.onResume()
        if (::overlayStatus.isInitialized) refreshOverlayStatus()
        if (::permissionStatus.isInitialized) refreshPermissionStatus()
        if (::listenStatus.isInitialized) refreshListenStatus()
        if (::modelStatus.isInitialized) refreshModelStatus()
        if (::sttStatus.isInitialized) refreshSttStatus()
    }

    /**
     * One line for the whole permission set, so the section is worth tapping.
     *
     * The grid this replaces gave no indication of what was already granted, so
     * the only way to find out was to open each of the eight screens in turn.
     */
    private fun refreshPermissionStatus() {
        val requirements = GrapheneCompat.requirements(this)
        val missing = requirements.count { it.essential && !it.satisfied }
        val optional = requirements.count { !it.essential && !it.satisfied }
        permissionStatus.text = when {
            missing > 0 -> "$missing required item(s) missing, $optional optional off."
            optional > 0 -> "Everything required is granted. $optional optional item(s) are off."
            else -> "Everything is granted."
        }
        permissionStatus.setTextColor(if (missing > 0) JarvisUi.GOLD else JarvisUi.DIM)
    }

    private fun refreshOverlayStatus() {
        val overlay = Settings.canDrawOverlays(this)
        val notify = GrapheneCompat.canPostNotifications(this)
        val fullScreen = GrapheneCompat.canUseFullScreenIntent(this)
        // Three separate grants decide whether a wake word puts anything on
        // screen, and until now the screen only mentioned one of them. Reported
        // together because the useful question is not "which of these do I
        // have" but "will saying my name do anything".
        overlayStatus.text = when {
            !notify ->
                "Notifications are OFF. Jarvis cannot show you anything at all — not the " +
                    "wake word, not an approval waiting for your answer. Turn them on from " +
                    "SYSTEM CHECK, under Permissions."
            overlay ->
                "Wake word: opens over whatever you are using. This is the good one — the " +
                    "orb is drawn directly, with no notification in the way."
            fullScreen ->
                "Wake word: takes over the screen via a full-screen notification. Turn on " +
                    "DISPLAY OVER APPS as well for the orb to be drawn directly instead."
            else ->
                "Wake word: arrives as a notification you have to TAP. Android will not let " +
                    "Jarvis put anything on screen by itself without DISPLAY OVER APPS above, " +
                    "or the full-screen grant in SYSTEM CHECK — on Android 14 the second is " +
                    "reserved for calling and alarm apps unless you grant it by hand."
        }
        overlayStatus.setTextColor(
            if (notify && (overlay || fullScreen)) JarvisUi.DIM else JarvisUi.GOLD
        )
    }


    /**
     * Whether always-on listening will survive a restart.
     *
     * Android refuses to start a foreground service typed `microphone` while
     * the app is in the background, and `BOOT_COMPLETED` is not an exemption
     * for that class of service. Two grants are: exemption from battery
     * optimisation, and "display over other apps". Either one is enough, so
     * this reports the pair as a single yes/no rather than making the user
     * reason about which.
     */
    private fun refreshListenStatus() {
        val exempt = isExemptFromBatteryOptimisation()
        val overlay = Settings.canDrawOverlays(this)
        val ok = exempt || overlay
        listenStatus.text = when {
            ok && exempt && overlay ->
                "Starts on its own: yes — battery exemption and overlay are both granted."
            ok && exempt ->
                "Starts on its own: yes — Jarvis is exempt from battery optimisation."
            ok ->
                "Starts on its own: yes — “display over other apps” covers it."
            else ->
                "Starts on its own: NO. Android will not let Jarvis open the microphone " +
                    "after a restart without one of these. Until you grant one, a reboot " +
                    "leaves a notification you have to tap before “Hey Jarvis” works again."
        }
        listenStatus.setTextColor(if (ok) JarvisUi.DIM else JarvisUi.GOLD)
    }

    /**
     * Whether the phone can do its own listening, and what it costs if it cannot.
     *
     * Two independent facts — the switch and the weights — and the interesting
     * state is the one where they disagree: the switch on with nothing
     * downloaded silently falls back to the server, which is exactly the shape
     * of bug where a privacy feature looks enabled and is not.
     */
    private fun refreshModelStatus() {
        val have = ModelStore.isDownloaded(this, OnDeviceWakeWord.REQUIRED_MODELS)
        val wanted = config.wakeWordOnDevice || wakeOnDevice.isChecked
        val megabytes = ModelStore.bytesOnDisk(this) / 1024.0 / 1024.0
        modelStatus.text = when {
            have && wanted ->
                "On-device detection is ready (%.1f MB). Nothing reaches your server until "
                    .format(megabytes) + "you say the name."
            have ->
                "Models are downloaded (%.1f MB) but detection is still on the server. "
                    .format(megabytes) + "Turn the switch above on to use them."
            wanted ->
                "Models are NOT downloaded, so detection is still happening on the server — " +
                    "which means the microphone is streaming there continuously. Tap " +
                    "DOWNLOAD MODELS."
            else -> "Models are not downloaded. About 3.6 MB, from your own server."
        }
        modelStatus.setTextColor(if (have && wanted) JarvisUi.DIM else JarvisUi.GOLD)
    }

    /**
     * Whether what the user asked for is what is happening.
     *
     * The setting is a preference; `LocalTranscriber.isAvailable` is a fact.
     * When they disagree the audio is still being streamed, and saying so is
     * the entire point — a privacy switch that looks on while the thing it
     * turns off is still running is worse than no switch.
     */
    private fun refreshSttStatus() {
        val possible = LocalTranscriber.isAvailable(this)
        val wanted = sttOnDevice.isChecked
        // Suspension first, because it OVERRIDES both of the others: with the
        // gate enforcing this path does not run whatever the switch says or
        // whatever the phone can do, and a line claiming "the recording never
        // leaves this phone" while every turn is being streamed would be the
        // one wrong thing this screen could say.
        val suspended = wanted && config.speakerGateEnforcing
        sttStatus.text = when {
            suspended ->
                "SUSPENDED while Jarvis is set to answer only your voice. That check runs " +
                    "on your server, on the sound; a turn transcribed here sends words, " +
                    "which cannot be checked — so audio is being streamed instead. " +
                    "Android's offline recogniser owns the microphone and never hands this " +
                    "app the audio, so the check cannot move here."
            wanted && possible ->
                "Speech is turned into text on this phone. The recording never leaves it — " +
                    "only the words do."
            wanted ->
                "This phone has NO offline speech recognition, so audio is still being sent " +
                    "to your server for every turn. Android needs an on-device recogniser " +
                    "and the language pack installed."
            else -> "Audio is streamed to your server, which transcribes it."
        }
        sttStatus.setTextColor(
            if (wanted && possible && !suspended) JarvisUi.DIM else JarvisUi.GOLD
        )
    }

    private fun downloadModels() {
        val check = ServerUrl.check(urlField.text.toString())
        val token = tokenField.text.toString().trim()
        if (!check.isValid || token.isEmpty()) {
            toast("Set the server URL and token first, and SAVE.")
            return
        }
        toast("Downloading from your server…")
        Thread {
            val problem = ModelStore.download(
                this,
                check.normalized,
                token,
                OnDeviceWakeWord.REQUIRED_MODELS,
            )
            runOnUiThread {
                toast(problem ?: "On-device models ready.")
                if (::modelStatus.isInitialized) refreshModelStatus()
                if (::sttStatus.isInitialized) refreshSttStatus()
                // Restart the listener so it picks the local path up now rather
                // than at the next reconnect.
                if (problem == null && config.wakeWordEnabled) {
                    runCatching { WakeWordService.ensureRunning(this, fromForeground = true) }
                }
            }
        }.start()
    }

    private fun deleteModels() {
        ModelStore.deleteAll(this)
        // Not just the files: leaving the switch on with nothing behind it is
        // the state this screen exists to make impossible.
        config.wakeWordOnDevice = false
        wakeOnDevice.isChecked = false
        refreshModelStatus()
        toast("Deleted. Detection is back on your server.")
    }

    private fun isExemptFromBatteryOptimisation(): Boolean = runCatching {
        getSystemService(PowerManager::class.java)
            ?.isIgnoringBatteryOptimizations(packageName) == true
    }.getOrDefault(false)

    /**
     * Ask to be left running in the background.
     *
     * The direct request dialog is the one that can be answered in place; some
     * ROMs remove it, so a failure falls back to the settings list where the
     * user can find Jarvis themselves.
     */
    // BatteryLife: asking for this outright is a Play-policy concern, and this
    // app is not on Play. An always-on microphone that Android may kill and
    // then refuse to restart is precisely what the exemption is for.
    @SuppressLint("BatteryLife")
    private fun requestBackgroundStart() {
        if (isExemptFromBatteryOptimisation()) {
            openSetting(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
            return
        }
        try {
            startActivity(
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                    .setData(Uri.fromParts("package", packageName, null))
            )
        } catch (e: ActivityNotFoundException) {
            openSetting(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
        }
    }

    // --- updates -----------------------------------------------------------

    private fun appVersionName(): String =
        runCatching { packageManager.getPackageInfo(packageName, 0).versionName }
            .getOrNull() ?: "?"

    /**
     * The installed build number, which is the only thing Android compares.
     *
     * `longVersionCode` since 28; minSdk is 29, so there is no legacy branch
     * to keep and no deprecation to suppress.
     */
    private fun appVersionCode(): Long =
        runCatching { packageManager.getPackageInfo(packageName, 0).longVersionCode }
            .getOrDefault(0L)

    /**
     * Ask GitHub for a newer build, and install it if the user agrees.
     *
     * On a plain thread rather than a coroutine: this Activity has no other
     * async machinery and one thread for one blocking check is less to explain
     * than a scope, a dispatcher and a lifecycle to cancel it against. The
     * result is posted back with `runOnUiThread`, and every path ends by
     * writing a sentence into [updateStatus] — a check that says nothing is
     * indistinguishable from a button that does nothing.
     */
    private fun checkForUpdates() {
        updateStatus.text = "Checking GitHub…"
        val allowPrerelease = prereleaseUpdates.isChecked
        val installed = appVersionCode()
        Thread {
            val checker = UpdateChecker(applicationContext)
            val found = checker.check(installed, allowPrerelease)
            if (found !is UpdateChecker.Result.Offered) {
                runOnUiThread { updateStatus.text = describe(found) }
                return@Thread
            }
            runOnUiThread {
                updateStatus.text = "Downloading ${found.update.versionName}…"
            }
            val installedResult = checker.install(found.update)
            runOnUiThread { updateStatus.text = describe(installedResult) }
        }.start()
    }

    private fun describe(result: UpdateChecker.Result): String = when (result) {
        is UpdateChecker.Result.UpToDate ->
            "Up to date — version ${appVersionName()} (build ${appVersionCode()})"
        is UpdateChecker.Result.Offered ->
            "${result.update.versionName} is available."
        // "Confirm the system prompt" was printed for the whole life of the app
        // by a build in which no prompt could appear — nothing received the
        // installer's STATUS_PENDING_USER_ACTION, so the activity that carries
        // was never started. InstallResultReceiver starts it now, and falls
        // back to a notification when Android refuses the background start.
        is UpdateChecker.Result.Handed ->
            "Downloaded ${result.update.versionName}. Confirm the install prompt — " +
                "if it does not appear, look in your notifications."
        is UpdateChecker.Result.Failed -> result.message
    }

    /**
     * "Install unknown apps" for this app.
     *
     * `ACTION_MANAGE_UNKNOWN_APP_SOURCES` wants a `package:` URI to land on
     * Jarvis's own switch; without it some ROMs open the full list and some
     * open nothing at all, so the plain action is the fallback rather than the
     * first try.
     */
    private fun openInstallPermission() {
        val direct = Intent(
            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
            Uri.parse("package:$packageName")
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            startActivity(direct)
        } catch (e: ActivityNotFoundException) {
            openSetting(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES)
        }
    }

    private fun openReleasesPage() {
        try {
            startActivity(
                Intent(
                    Intent.ACTION_VIEW,
                    Uri.parse("https://github.com/${UpdateChecker.DEFAULT_REPO}/releases")
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: ActivityNotFoundException) {
            toast("No browser to open the releases page")
        }
    }

    // --- system settings deep links ----------------------------------------

    private fun openSetting(action: String) {
        try {
            startActivity(Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: ActivityNotFoundException) {
            toast("This device has no screen for that setting")
        }
    }

    private fun openOverlaySetting() {
        try {
            startActivity(
                Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:$packageName")
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: ActivityNotFoundException) {
            toast("This device has no screen for that setting")
        }
    }

    private fun openAppInfo() {
        try {
            startActivity(
                Intent(
                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:$packageName")
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: ActivityNotFoundException) {
            toast("This device has no screen for that setting")
        }
    }

    // --- small builders -----------------------------------------------------

    private fun matchWidth() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT
    )

    private fun row(vararg children: android.view.View): LinearLayout =
        LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, JarvisUi.dp(this@SettingsActivity, 8), 0, 0)
            children.forEachIndexed { index, child ->
                if (index > 0) {
                    addView(
                        android.view.View(this@SettingsActivity),
                        LinearLayout.LayoutParams(JarvisUi.dp(this@SettingsActivity, 10), 1)
                    )
                }
                addView(
                    child,
                    LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                )
            }
        }

    private fun switchRow(ctx: Context, label: String, checked: Boolean): Switch =
        Switch(ctx).apply {
            text = label
            isChecked = checked
            setTextColor(JarvisUi.DIM)
            textSize = 14f
            setPadding(0, JarvisUi.dp(ctx, 10), 0, JarvisUi.dp(ctx, 2))
        }

    private fun toast(message: String) =
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    companion object {
        private const val REQ_SCAN = 5001

        /** The de-facto standard scan intent; F-Droid scanners answer it. */
        private const val ZXING_SCAN = "com.google.zxing.client.android.SCAN"
    }
}
