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
import android.view.View
import android.view.ViewGroup
import android.widget.Button
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
        setContentView(buildUi().also { JarvisUi.fitSystemBars(it) })
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

        // One control for the fourteen paragraphs below it. See [explain].
        explanationsToggle = JarvisUi.ghost(ctx, explanationsLabel()) { toggleExplanations() }
        col.addView(
            explanationsToggle,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = JarvisUi.dp(ctx, 12) },
        )

        // --- server ---------------------------------------------------------

        col.addView(JarvisUi.label(ctx, "Server URL"))
        urlField = JarvisUi.field(ctx, "http://192.168.2.10:8123", config.serverUrl)
        col.addView(urlField, matchWidth())
        col.addView(
            explain(ctx, getString(R.string.settings_server_url_explain))
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
            explain(ctx, getString(R.string.settings_token_explain))
        )

        col.addView(JarvisUi.label(ctx, "Pipeline name"))
        pipelineField = JarvisUi.field(ctx, JarvisConfig.DEFAULT_PIPELINE, config.pipeline)
        col.addView(pipelineField, matchWidth())

        col.addView(JarvisUi.label(ctx, "Device name"))
        deviceNameField = JarvisUi.field(ctx, "This phone", config.deviceName)
        col.addView(deviceNameField, matchWidth())
        col.addView(
            explain(ctx, getString(R.string.settings_device_name_explain, config.deviceId))
        )

        // --- voice ----------------------------------------------------------

        col.addView(JarvisUi.label(ctx, "Voice"))
        wakeEnabled = switchRow(ctx, "Listen for \"Hey Jarvis\"", config.wakeWordEnabled)
        col.addView(wakeEnabled, matchWidth())
        col.addView(
            explain(ctx, getString(R.string.settings_wake_word_explain))
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
            explain(ctx, getString(R.string.settings_on_device_explain))
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
            explain(ctx, getString(R.string.settings_stt_explain))
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
            explain(ctx, getString(R.string.settings_car_explain))
        )

        // --- whose voice ------------------------------------------------------
        col.addView(JarvisUi.spacer(ctx, 12))
        col.addView(JarvisUi.label(ctx, "Whose voice"))
        col.addView(
            explain(ctx, getString(R.string.settings_voice_identity_explain))
        )
        col.addView(
            JarvisUi.ghost(ctx, "TEACH JARVIS MY VOICE") {
                startActivity(Intent(this, VoiceIdentityActivity::class.java))
            },
            matchWidth()
        )
        col.addView(
            explain(ctx, getString(R.string.settings_voice_identity_conflict_explain))
        )

        // --- when to listen ---------------------------------------------------
        //
        // This section was labelled "saved, not yet in effect" and it was
        // telling the truth: `WakeWordGate` implemented the whole policy,
        // `shouldListen` had no production caller, and four preference keys were
        // written here and read by nothing. `WakeListenWatch` is the missing
        // half — it gathers the signals and consults the gate before every
        // microphone open — so the label goes.
        //
        // The one thing that has NOT changed is that "am I at home" is usually
        // unknowable on a phone. What changed is that it is now modelled as
        // unknown rather than as false, and the explanation below says exactly
        // what happens then. See `WakeWordGate.decide`.

        col.addView(JarvisUi.spacer(ctx, 12))
        col.addView(JarvisUi.label(ctx, "When to listen"))
        wakeInCar = switchRow(ctx, "While car Bluetooth is connected", config.wakeInCar)
        col.addView(wakeInCar, matchWidth())
        wakeAtHome = switchRow(ctx, "While at home, during waking hours", config.wakeAtHome)
        col.addView(wakeAtHome, matchWidth())
        col.addView(hourRow(ctx), matchWidth())
        col.addView(explain(ctx, getString(R.string.settings_when_to_listen_explain)))

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
            explain(ctx, getString(R.string.settings_headset_explain))
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
            explain(ctx, getString(R.string.settings_permissions_explain))
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
            explain(ctx, getString(R.string.settings_more_explain))
        )
        col.addView(
            row(
                JarvisUi.ghost(ctx, "CRASH LOGS") {
                    startActivity(Intent(this, ai.jarvis.app.ui.CrashLogActivity::class.java))
                },
                // SHARES A ROW rather than taking one of its own.
                //
                // SAVE is the last control on a screen that is already several
                // screens long, and the instrumented suite reaches it by
                // scrolling a bounded number of steps. A full-width button plus
                // a paragraph of its own added just enough height above SAVE to
                // put it out of that reach: three SettingsPersistenceTest cases
                // went from passing to "No SAVE button on screen" on the run
                // that introduced them, over something none of them tests. A
                // row that already exists costs no height, and the sentence
                // explaining it goes in the hint that was already here.
                JarvisUi.ghost(ctx, "APPROVALS") {
                    JarvisScreens.open(this, JarvisScreens.ACTION_POLICY, "Action approvals")
                },
            )
        )
        col.addView(
            explain(ctx, getString(R.string.settings_logs_explain))
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
                // NOT startActivity directly. SAVE is the last control on a
                // screen several screens long, and this strip is at the very
                // top: tapping a tab after editing the token used to leave the
                // screen and discard every edited field, silently, with the
                // typing still on screen behind the new activity for a frame.
                // See [leaveIfSaved].
                leaveIfSaved { startActivity(ManagementActivity.intent(this, tab)) }
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

    // --- leaving with unsaved edits -----------------------------------------

    /**
     * Every field's saved value, as one comparable string.
     *
     * Compared against the same function run over the *controls* — see
     * [isDirty]. A snapshot rather than a per-control dirty flag because there
     * are eleven of them across four types (EditText, Switch, two chooser
     * indices) and a flag on each is eleven places to forget one; this is one
     * place, and adding a control that is not in it is a visible omission rather
     * than a silent one.
     */
    private fun savedSnapshot(): String = listOf(
        ServerUrl.normalize(config.serverUrl),
        config.token,
        config.pipeline,
        config.deviceName,
        config.allowPrereleaseUpdates,
        config.wakeWordEnabled,
        config.wakeWordOnDevice,
        config.sttOnDevice,
        config.wakeInCar,
        config.wakeAtHome,
        config.wakingHourStart,
        config.wakingHourEnd,
        config.headsetMode,
        config.headsetButton,
        config.warmLink,
    ).joinToString(" ")

    private fun editedSnapshot(): String = listOf(
        ServerUrl.normalize(urlField.text.toString()),
        tokenField.text.toString().trim(),
        // Through the same normalisation `save` applies, or an empty pipeline
        // box would read as an edit against the default it is about to become.
        pipelineField.text.toString().trim().ifEmpty { JarvisConfig.DEFAULT_PIPELINE },
        deviceNameField.text.toString().trim().ifEmpty { config.deviceName },
        prereleaseUpdates.isChecked,
        wakeEnabled.isChecked,
        wakeOnDevice.isChecked,
        sttOnDevice.isChecked,
        wakeInCar.isChecked,
        wakeAtHome.isChecked,
        wakingStart,
        wakingEnd,
        headsetMode.isChecked,
        // The two that read `headsetMode` in their own getters, mirrored here so
        // the comparison sees what `save` will actually store rather than what
        // the switch shows.
        headsetMode.isChecked && headsetButton.isChecked,
        headsetMode.isChecked && headsetWarmLink.isChecked,
    ).joinToString(" ")

    /**
     * True when something on screen differs from what is stored.
     *
     * Reported as settings silently vanishing: the console tab strip at the top
     * of this screen went straight to `startActivity`, and Back went straight
     * out, while `save()` only ever ran from the SAVE pill at the bottom of a
     * long ScrollView. Editing the server URL and then tapping any tab — the
     * most natural thing to do on a screen with a nav bar across the top —
     * discarded the edit with no warning and no way to get it back.
     */
    private fun isDirty(): Boolean = try {
        savedSnapshot() != editedSnapshot()
    } catch (t: Throwable) {
        // A control that has not been built yet cannot be dirty, and a
        // half-constructed screen must not trap the user on it.
        false
    }

    /**
     * Run [go], having given the user a chance to keep their edits.
     *
     * Three answers, and the third is the one that matters: SAVE runs the same
     * validation the pill does, so leaving cannot store an invalid URL by the
     * side door — if it refuses, the user stays here with the error, which is
     * the correct outcome and not a dropped navigation.
     */
    private fun leaveIfSaved(go: () -> Unit) {
        if (!isDirty()) {
            go()
            return
        }
        android.app.AlertDialog.Builder(this)
            .setTitle("Unsaved changes")
            .setMessage(
                "You have changed something on this screen. Leaving now discards it."
            )
            .setPositiveButton("SAVE") { _, _ ->
                // `save()` finishes this activity on success, so the
                // destination is started from the same place SAVE lands.
                if (save()) go()
            }
            .setNegativeButton("DISCARD") { _, _ -> go() }
            .setNeutralButton("KEEP EDITING", null)
            .show()
    }

    @Deprecated("Predictive back is disabled in the manifest, so this is the back path.")
    override fun onBackPressed() {
        // Back is the other way off this screen, and it discarded edits exactly
        // as silently as the tab strip did.
        //
        // `finish()` and not `super.onBackPressed()`: Kotlin refuses a `super`
        // call from inside a lambda, and this one has to happen after the user
        // answers a dialog. They are the same thing here anyway — this activity
        // has no fragment back stack, and predictive back is off in the
        // manifest, so the default Back behaviour IS finishing.
        leaveIfSaved { finish() }
    }

    // --- persistence --------------------------------------------------------

    /**
     * @return true when everything was stored. False means a validation refused
     *   it and the screen has said why — which [leaveIfSaved] needs, because
     *   "SAVE and then leave" must not leave when the save did not happen.
     */
    private fun save(): Boolean {
        val check = ServerUrl.check(urlField.text.toString())
        if (!check.isValid) {
            toast(check.error ?: "Invalid server URL")
            return false
        }
        val token = tokenField.text.toString().trim()
        if (token.isEmpty()) {
            toast("An access token is required")
            return false
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
        return true
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
            missing > 0 -> getString(R.string.status_permissions_missing, missing, optional)
            optional > 0 -> getString(R.string.status_permissions_optional, optional)
            else -> getString(R.string.status_permissions_ok)
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
        overlayStatus.text = getString(
            when {
                !notify -> R.string.status_overlay_no_notifications
                overlay -> R.string.status_overlay_granted
                fullScreen -> R.string.status_overlay_full_screen
                else -> R.string.status_overlay_none
            }
        )
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
        listenStatus.text = getString(
            when {
                ok && exempt && overlay -> R.string.status_listen_both
                ok && exempt -> R.string.status_listen_battery
                ok -> R.string.status_listen_overlay
                else -> R.string.status_listen_no
            }
        )
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
            have && wanted -> getString(R.string.status_models_ready, megabytes)
            have -> getString(R.string.status_models_unused, megabytes)
            wanted -> getString(R.string.status_models_wanted)
            else -> getString(R.string.status_models_absent)
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
        sttStatus.text = getString(
            when {
                suspended -> R.string.status_stt_suspended
                wanted && possible -> R.string.status_stt_local
                wanted -> R.string.status_stt_unavailable
                else -> R.string.status_stt_streamed
            }
        )
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


    // --- explanations, folded away by default -------------------------------

    /**
     * Every explanatory paragraph on this screen, so one control can hide them.
     *
     * Reported as *"clean up the settings in the android app, there's a lot of
     * text, buttons are too close together, it's hard to understand/navigate"*.
     * There are fourteen sections here and fourteen paragraphs explaining them —
     * around 570 words — and every one of them is worth reading ONCE. Read
     * every time, they are what makes the screen hard to navigate: the labels
     * and the controls, which are the things somebody came here to find, are
     * separated by prose they have already read.
     *
     * So nothing is deleted and nothing is shortened. It starts folded, and one
     * control at the top unfolds all of it. Deliberately not persisted: the
     * default is the state that makes the screen usable, and a person who wants
     * the prose wants it for the visit they are on.
     */
    private val explanations = mutableListOf<TextView>()

    private var explanationsShown = false

    private lateinit var explanationsToggle: Button

    /** [JarvisUi.hint], remembered, and hidden until asked for. */
    private fun explain(context: Context, text: String): TextView =
        JarvisUi.hint(context, text).also {
            it.visibility = if (explanationsShown) View.VISIBLE else View.GONE
            explanations += it
        }

    private fun toggleExplanations() {
        explanationsShown = !explanationsShown
        val visibility = if (explanationsShown) View.VISIBLE else View.GONE
        for (view in explanations) view.visibility = visibility
        explanationsToggle.text = explanationsLabel()
    }

    private fun explanationsLabel(): String =
        if (explanationsShown) "HIDE EXPLANATIONS" else "WHAT DOES ALL THIS DO?"

    private fun row(vararg children: android.view.View): LinearLayout =
        LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            // 14, not 8. Reported as "buttons are too close together": a row
            // of outlined ghost buttons with 8dp above it and 10dp between
            // reads as one control with lines through it rather than two.
            setPadding(0, JarvisUi.dp(this@SettingsActivity, 14), 0, 0)
            children.forEachIndexed { index, child ->
                if (index > 0) {
                    addView(
                        android.view.View(this@SettingsActivity),
                        LinearLayout.LayoutParams(JarvisUi.dp(this@SettingsActivity, 16), 1)
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
