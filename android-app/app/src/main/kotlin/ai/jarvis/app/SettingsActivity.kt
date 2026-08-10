package ai.jarvis.app

import ai.jarvis.app.assist.WakeWordService
import ai.jarvis.app.automation.JarvisAutomationService
import ai.jarvis.app.automation.actions.ActionEnv
import ai.jarvis.app.channel.DeviceChannelHost
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.config.ServerUrl
import ai.jarvis.app.update.UpdateChecker
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
import android.text.InputType
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
    private lateinit var updateStatus: TextView
    private lateinit var prereleaseUpdates: Switch
    private lateinit var wakeEnabled: Switch
    private lateinit var wakeInCar: Switch
    private lateinit var wakeAtHome: Switch
    private lateinit var wakeStartField: EditText
    private lateinit var wakeEndField: EditText

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

        // --- wake word ------------------------------------------------------

        col.addView(JarvisUi.label(ctx, "Wake word"))
        col.addView(
            JarvisUi.hint(
                ctx,
                "Android gives third-party apps no low-power hotword path, so always-on " +
                    "detection means a genuinely open mic and real battery cost. These options " +
                    "limit when that happens."
            )
        )
        wakeEnabled = switchRow(ctx, "Listen for \"Hey Jarvis\"", config.wakeWordEnabled)
        col.addView(wakeEnabled, matchWidth())
        wakeInCar = switchRow(ctx, "…while car Bluetooth is connected", config.wakeInCar)
        col.addView(wakeInCar, matchWidth())
        wakeAtHome = switchRow(ctx, "…while at home, during waking hours", config.wakeAtHome)
        col.addView(wakeAtHome, matchWidth())

        val hours = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        wakeStartField = hourField(config.wakingHourStart)
        wakeEndField = hourField(config.wakingHourEnd)
        hours.addView(
            TextView(ctx).apply {
                text = "Waking hours"
                setTextColor(JarvisUi.DIM)
                textSize = 14f
            },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        hours.addView(wakeStartField, LinearLayout.LayoutParams(JarvisUi.dp(ctx, 64), ViewGroup.LayoutParams.WRAP_CONTENT))
        hours.addView(
            TextView(ctx).apply {
                text = " to "
                setTextColor(JarvisUi.FAINT)
                textSize = 14f
            }
        )
        hours.addView(wakeEndField, LinearLayout.LayoutParams(JarvisUi.dp(ctx, 64), ViewGroup.LayoutParams.WRAP_CONTENT))
        col.addView(hours, matchWidth())
        col.addView(JarvisUi.hint(ctx, "Hours are 0-23 for the start and 0-24 for the end; a window that wraps midnight (22 to 6) is fine."))
        // Said out loud rather than left to be discovered. `WakeWordGate` needs
        // to know whether the phone is at home, and nothing on this device
        // produces that signal yet — there is no home-presence source anywhere
        // in the app. Enforcing the gate without one would mean "not at home"
        // always, which would silence the wake word everywhere except a car.
        // So these three are stored and not yet applied, and a settings screen
        // that implied otherwise would be lying.
        col.addView(
            JarvisUi.hint(
                ctx,
                "The three limits above are saved but not yet enforced: Jarvis has no " +
                    "home-presence signal on this device, so applying them would silence " +
                    "the wake word everywhere except the car. Until that exists, the " +
                    "master switch is what decides."
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

        // --- other screens --------------------------------------------------

        col.addView(JarvisUi.label(ctx, "More"))
        col.addView(
            row(
                JarvisUi.ghost(ctx, "AUTOMATIONS") {
                    JarvisScreens.open(this, JarvisScreens.AUTOMATIONS, "Automations")
                },
                JarvisUi.ghost(ctx, "AUDIT LOG") {
                    JarvisScreens.open(this, JarvisScreens.AUDIT_LOG, "The audit log")
                },
            )
        )
        col.addView(
            JarvisUi.hint(
                ctx,
                "The audit log records every action this device actually executed, with its " +
                    "tier and how it was authorised. It is local and yours."
            )
        )
        col.addView(
            row(
                JarvisUi.ghost(ctx, "SYSTEM CHECK") {
                    startActivity(Intent(this, ai.jarvis.app.ui.SystemCheckActivity::class.java))
                },
                JarvisUi.ghost(ctx, "CRASH LOGS") {
                    startActivity(Intent(this, ai.jarvis.app.ui.CrashLogActivity::class.java))
                },
            )
        )
        col.addView(
            JarvisUi.hint(
                ctx,
                "System check lists every permission and special access Jarvis can use and what " +
                    "breaks without it. Crash logs are written to this device and go nowhere else."
            )
        )

        // --- system access --------------------------------------------------

        col.addView(JarvisUi.label(ctx, "System access"))
        col.addView(
            JarvisUi.hint(
                ctx,
                "Each of these is off until you turn it on, and none of them changes what tier " +
                    "an action is — they only decide whether it is possible at all."
            )
        )
        col.addView(
            row(
                JarvisUi.ghost(ctx, "ASSISTANT") { openSetting(Settings.ACTION_VOICE_INPUT_SETTINGS) },
                JarvisUi.ghost(ctx, "ACCESSIBILITY") { openSetting(Settings.ACTION_ACCESSIBILITY_SETTINGS) },
            )
        )
        col.addView(
            row(
                JarvisUi.ghost(ctx, "NOTIFICATIONS") {
                    openSetting(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
                },
                JarvisUi.ghost(ctx, "OVERLAY") { openOverlaySetting() },
            )
        )
        // Said out loud because nothing else does. "Display over other apps" is
        // the one permission that decides whether a wake word can put Jarvis in
        // front of whatever you are looking at: without it Android silently
        // drops the background activity start — silently, so a try/catch never
        // sees it — and the conversation arrives as a notification you have to
        // tap. The switch is a Settings trip, not a prompt, so nobody finds it
        // by accident.
        overlayStatus = TextView(ctx).apply {
            setTextColor(JarvisUi.DIM)
            textSize = 12f
        }
        col.addView(overlayStatus)
        col.addView(
            row(
                JarvisUi.ghost(ctx, "BATTERY") {
                    openSetting(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
                },
                JarvisUi.ghost(ctx, "APP INFO") { openAppInfo() },
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
        root.addView(
            scroll,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        return root
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
        config.wakeInCar = wakeInCar.isChecked
        config.wakeAtHome = wakeAtHome.isChecked
        config.wakingHourStart = wakeStartField.text.toString().trim().toIntOrNull()
            ?: config.wakingHourStart
        config.wakingHourEnd = wakeEndField.text.toString().trim().toIntOrNull()
            ?: config.wakingHourEnd

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
        tokenField.setText(scanned)
        toast("Scanned ${scanned.length} characters")
    }

    /**
     * Re-read on resume: granting the permission happens in another app, so the
     * only moment this can be correct is when we come back from it.
     */
    override fun onResume() {
        super.onResume()
        if (::overlayStatus.isInitialized) refreshOverlayStatus()
        if (::listenStatus.isInitialized) refreshListenStatus()
    }

    private fun refreshOverlayStatus() {
        val granted = Settings.canDrawOverlays(this)
        overlayStatus.text = if (granted) {
            "Display over other apps: on — “Hey Jarvis” can open over whatever you are using."
        } else {
            "Display over other apps: OFF — a wake word will arrive as a notification " +
                "instead of opening over the app you are in. Tap OVERLAY to change it."
        }
        overlayStatus.setTextColor(if (granted) JarvisUi.DIM else JarvisUi.GOLD)
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
            "Ready to install ${result.update.versionName} — confirm the system prompt."
        is UpdateChecker.Result.Failed -> result.message
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

    private fun hourField(value: Int): EditText = EditText(this).apply {
        setText(value.toString())
        inputType = InputType.TYPE_CLASS_NUMBER
        setSingleLine(true)
        gravity = Gravity.CENTER
        setTextColor(android.graphics.Color.WHITE)
        setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, 15f)
        background = JarvisUi.panel(this@SettingsActivity, fill = 0xFF080D13.toInt())
        setPadding(
            JarvisUi.dp(this@SettingsActivity, 8), JarvisUi.dp(this@SettingsActivity, 10),
            JarvisUi.dp(this@SettingsActivity, 8), JarvisUi.dp(this@SettingsActivity, 10)
        )
    }

    private fun toast(message: String) =
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    companion object {
        private const val REQ_SCAN = 5001

        /** The de-facto standard scan intent; F-Droid scanners answer it. */
        private const val ZXING_SCAN = "com.google.zxing.client.android.SCAN"
    }
}
