package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.intOr
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.Build
import android.provider.Settings
import android.view.KeyEvent
import android.widget.Toast
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * The Tasker rows that were gaps (M61, `docs/ANDROID_TASKER_PARITY.md`),
 * closed here: the ones a phone can do with a permission the app already
 * asks for or a system service any app may call. Each action's arithmetic —
 * what a parameter means, what is refused — is a pure function beside it,
 * so `ParityActionsTest` proves it on the JVM without a device.
 *
 * Not here, on purpose: the rows that need a permission this app does not
 * yet request (camera, SMS, call log, NFC) or a listener it does not run
 * (now-playing). They stay marked gap until a handset can prove them.
 */

/** Tier 1 — a line of text on the screen for a moment; nothing changes. */
object ShowToast : JarvisAction {
    override val id = "show_toast"
    override val tier = ActionTier.AUTO
    override val description = "Flash a short line of text on the screen (a toast)."
    override val paramsSchema = mapOf(
        "text" to "string: what to show (at most 200 characters)",
        "long" to "bool (optional): keep it up longer"
    )
    override val capability = "device"

    /** The text as it will be shown, or null when there is nothing to show. */
    fun textOf(params: JSONObject): String? = params.str("text")?.trim()?.take(MAX_CHARS)?.takeIf { it.isNotEmpty() }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val text = textOf(params) ?: return ActionResult.error("text is required")
        val long = params.optBoolean("long", false)
        withContext(Dispatchers.Main) {
            Toast.makeText(ctx, text, if (long) Toast.LENGTH_LONG else Toast.LENGTH_SHORT).show()
        }
        return ActionResult.ok(json("text" to text, "long" to long))
    }

    const val MAX_CHARS = 200
}

/** The three system settings a phone owner changes most: written only with WRITE_SETTINGS. */
internal object SystemSettings {
    fun canWrite(ctx: Context): Boolean = Settings.System.canWrite(ctx)

    const val NEEDS_GRANT = "changing system settings needs the 'Modify system settings' " +
        "permission; grant it in Settings > Apps > Jarvis > Modify system settings"
}

/** Tier 1 — auto-brightness on or off. */
object SetAutoBrightness : JarvisAction {
    override val id = "set_auto_brightness"
    override val tier = ActionTier.AUTO
    override val description = "Turn adaptive (automatic) brightness on or off."
    override val paramsSchema = mapOf("on" to "bool: true for automatic, false for manual")
    override val capability = "device"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (!params.has("on")) return ActionResult.error("on (true/false) is required")
        if (!SystemSettings.canWrite(ctx)) return ActionResult.error(SystemSettings.NEEDS_GRANT)
        val on = params.optBoolean("on")
        val mode = if (on) Settings.System.SCREEN_BRIGHTNESS_MODE_AUTOMATIC else Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL
        Settings.System.putInt(ctx.contentResolver, Settings.System.SCREEN_BRIGHTNESS_MODE, mode)
        return ActionResult.ok(json("on" to on))
    }
}

/** Tier 1 — rotation lock on or off. */
object SetRotationLock : JarvisAction {
    override val id = "set_rotation_lock"
    override val tier = ActionTier.AUTO
    override val description = "Lock the screen orientation (true) or let it rotate (false)."
    override val paramsSchema = mapOf("locked" to "bool: true to lock rotation")
    override val capability = "device"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (!params.has("locked")) return ActionResult.error("locked (true/false) is required")
        if (!SystemSettings.canWrite(ctx)) return ActionResult.error(SystemSettings.NEEDS_GRANT)
        val locked = params.optBoolean("locked")
        Settings.System.putInt(ctx.contentResolver, Settings.System.ACCELEROMETER_ROTATION, if (locked) 0 else 1)
        return ActionResult.ok(json("locked" to locked))
    }
}

/** Tier 1 — how long the screen stays on. */
object SetScreenTimeout : JarvisAction {
    override val id = "set_screen_timeout"
    override val tier = ActionTier.AUTO
    override val description = "Set how many seconds the screen stays on after the last touch."
    override val paramsSchema = mapOf("seconds" to "int 15-1800: the timeout")
    override val capability = "device"

    /** The timeout in seconds, or null when it is missing or outside the range a phone accepts. */
    fun secondsOf(params: JSONObject): Int? {
        if (!params.has("seconds")) return null
        val seconds = params.intOr("seconds", -1)
        return seconds.takeIf { it in MIN_SECONDS..MAX_SECONDS }
    }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val seconds = secondsOf(params) ?: return ActionResult.error("seconds must be between $MIN_SECONDS and $MAX_SECONDS")
        if (!SystemSettings.canWrite(ctx)) return ActionResult.error(SystemSettings.NEEDS_GRANT)
        Settings.System.putInt(ctx.contentResolver, Settings.System.SCREEN_OFF_TIMEOUT, seconds * 1000)
        return ActionResult.ok(json("seconds" to seconds))
    }

    const val MIN_SECONDS = 15
    const val MAX_SECONDS = 1800
}

/** Tier 1 — what the phone is connected to. */
object GetNetworkInfo : JarvisAction {
    override val id = "get_network_info"
    override val tier = ActionTier.AUTO
    override val description = "The current network: transport (wifi/cellular/none), whether it reaches the internet, the Wi-Fi name and signal when known."
    override val paramsSchema = emptyMap<String, String>()
    override val capability = "device"

    /** The transport in a word, from the capabilities a network reports. */
    fun transportOf(wifi: Boolean, cellular: Boolean, ethernet: Boolean, vpn: Boolean): String = when {
        vpn -> "vpn"
        wifi -> "wifi"
        cellular -> "cellular"
        ethernet -> "ethernet"
        else -> "none"
    }

    /** Android hides the SSID behind location: an unknown one is said, not guessed. */
    fun ssidOf(raw: String?): String? = raw?.trim('"')?.takeIf { it.isNotEmpty() && it != "<unknown ssid>" }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return ActionResult.error("no connectivity service")
        val network = cm.activeNetwork
        val caps = network?.let { cm.getNetworkCapabilities(it) }
        val transport = transportOf(
            caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true,
            caps?.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) == true,
            caps?.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) == true,
            caps?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true,
        )
        val internet = caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED) == true
        val out = json("transport" to transport, "internet" to internet)
        if (transport == "wifi") {
            @Suppress("DEPRECATION")
            val wifi = ctx.applicationContext.getSystemService(WifiManager::class.java)?.connectionInfo
            val ssid = ssidOf(wifi?.ssid)
            out.put("ssid", ssid ?: JSONObject.NULL)
            if (ssid == null) out.put("ssid_note", "the Wi-Fi name needs the location permission on this Android")
            if (wifi != null) out.put("rssi_dbm", wifi.rssi)
        }
        return ActionResult.ok(out)
    }
}

/** Tier 3 — an arbitrary intent reaches any app; the user sees exactly what is sent. */
object SendIntent : JarvisAction {
    override val id = "send_intent"
    override val tier = ActionTier.CONFIRM
    override val description = "Send an Android intent: an action string, optional data URI, package and string extras. Starts an activity."
    override val paramsSchema = mapOf(
        "action" to "string: e.g. android.intent.action.VIEW",
        "data" to "string (optional): a URI",
        "package" to "string (optional): restrict to one app",
        "extras" to "object (optional): string extras"
    )
    override val capability = "apps"

    /** What would be sent, validated, or an error message. */
    fun parse(params: JSONObject): Pair<Parsed?, String?> {
        val action = params.str("action")?.trim().orEmpty()
        if (action.isEmpty()) return null to "action is required"
        if (!action.contains('.')) return null to "action must be a fully qualified intent action, e.g. android.intent.action.VIEW"
        val extras = LinkedHashMap<String, String>()
        params.optJSONObject("extras")?.let { obj ->
            for (key in obj.keys()) extras[key] = obj.optString(key)
        }
        return Parsed(action, params.str("data")?.trim()?.takeIf { it.isNotEmpty() }, params.str("package")?.trim()?.takeIf { it.isNotEmpty() }, extras) to null
    }

    data class Parsed(val action: String, val data: String?, val pkg: String?, val extras: Map<String, String>)

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val (parsed, error) = parse(params)
        if (parsed == null) return ActionResult.error(error ?: "bad intent")
        val intent = Intent(parsed.action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        parsed.data?.let { intent.data = android.net.Uri.parse(it) }
        parsed.pkg?.let { intent.setPackage(it) }
        for ((k, v) in parsed.extras) intent.putExtra(k, v)
        return try {
            ctx.startActivity(intent)
            ActionResult.ok(json("action" to parsed.action, "data" to parsed.data, "package" to parsed.pkg))
        } catch (e: android.content.ActivityNotFoundException) {
            ActionResult.error("nothing on this phone handles ${parsed.action}")
        } catch (e: SecurityException) {
            ActionResult.error("not allowed to send ${parsed.action}: ${e.message}")
        }
    }
}

/** Tier 1 — an app's own shortcut, by package and shortcut id. */
object LaunchShortcut : JarvisAction {
    override val id = "launch_shortcut"
    override val tier = ActionTier.AUTO
    override val description = "Open one of an app's static or pinned shortcuts (as the launcher would)."
    override val paramsSchema = mapOf(
        "package" to "string: the app",
        "shortcut_id" to "string: the shortcut's id"
    )
    override val capability = "apps"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val pkg = params.str("package")?.trim().orEmpty()
        val shortcut = params.str("shortcut_id")?.trim().orEmpty()
        if (pkg.isEmpty() || shortcut.isEmpty()) return ActionResult.error("package and shortcut_id are required")
        val launcher = ctx.getSystemService(android.content.pm.LauncherApps::class.java)
            ?: return ActionResult.error("no launcher service")
        return try {
            launcher.startShortcut(pkg, shortcut, null, null, android.os.Process.myUserHandle())
            ActionResult.ok(json("package" to pkg, "shortcut_id" to shortcut))
        } catch (e: SecurityException) {
            // Only the default launcher may start another app's shortcuts.
            ActionResult.error("starting shortcuts needs Jarvis to be the default launcher")
        } catch (e: IllegalStateException) {
            ActionResult.error("the shortcut is not available: ${e.message}")
        }
    }
}

/** Tier 1 — the media keys, sent to whatever is playing. */
object MediaControl : JarvisAction {
    override val id = "media_control"
    override val tier = ActionTier.AUTO
    override val description = "Play, pause, toggle, stop, next or previous — sent to whichever app is playing, like the headset button."
    override val paramsSchema = mapOf("command" to "string: play | pause | toggle | stop | next | previous")
    override val capability = "media"

    /** The key code for a command, or null when the word is not one. */
    fun keyFor(command: String?): Int? = when (command?.trim()?.lowercase()) {
        "play" -> KeyEvent.KEYCODE_MEDIA_PLAY
        "pause" -> KeyEvent.KEYCODE_MEDIA_PAUSE
        "toggle", "play_pause", "playpause" -> KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
        "stop" -> KeyEvent.KEYCODE_MEDIA_STOP
        "next", "skip" -> KeyEvent.KEYCODE_MEDIA_NEXT
        "previous", "prev", "back" -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
        else -> null
    }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val command = params.str("command")
        val key = keyFor(command) ?: return ActionResult.error("command must be play, pause, toggle, stop, next or previous")
        val am = ctx.getSystemService(AudioManager::class.java) ?: return ActionResult.error("no audio service")
        am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, key))
        am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, key))
        return ActionResult.ok(json("command" to command?.trim()?.lowercase(), "playing" to am.isMusicActive))
    }
}

/** Every action this file closes, for the registry and the tests. */
object ParityActions {
    val all: List<JarvisAction> = listOf(
        ShowToast, SetAutoBrightness, SetRotationLock, SetScreenTimeout,
        GetNetworkInfo, SendIntent, LaunchShortcut, MediaControl,
    )

    /** Rows the accessibility agent already closes under other ids. */
    val aliases: Map<String, String> = mapOf(
        "lock_screen" to "ui_global_action",
        "screenshot" to "take_screenshot",
    )

    @Suppress("unused")
    private val sdk = Build.VERSION.SDK_INT
}
