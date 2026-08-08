package ai.jarvis.app.automation.actions.builtin

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.SsrfGuard
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * Apps and intents.
 *
 * Starting an activity from a background service is restricted from Android 10
 * onwards. Jarvis normally has a foreground service or an overlay permission,
 * but when the platform refuses, these actions say so plainly instead of
 * pretending the app opened.
 *
 * Package visibility (Android 11+) also applies: resolving apps by name needs a
 * `<queries>` element or QUERY_ALL_PACKAGES in the manifest. See docs/actions.md.
 */

private fun startActivity(ctx: Context, intent: Intent, what: String): ActionResult = try {
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    ctx.startActivity(intent)
    ActionResult.ok(json("started" to what))
} catch (e: ActivityNotFoundException) {
    ActionResult.error("nothing on this phone can handle $what")
} catch (e: SecurityException) {
    ActionResult.error("the system refused to start $what: ${e.message ?: "background launch blocked"}")
}

/** Tier 1 — "launch an app" is named as Tier 1 in the shared brief. */
object LaunchApp : JarvisAction {
    override val id = "launch_app"
    override val tier = ActionTier.AUTO
    override val description = "Open an app by package name, or by its visible name."
    override val paramsSchema = mapOf(
        "package" to "string: exact package id, e.g. org.mozilla.fenix",
        "name" to "string: app label to match instead, e.g. 'Signal'"
    )
    override val capability = "apps"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            val pm = ctx.packageManager
            val explicit = params.str("package")
            val label = params.str("name")
            val pkg = when {
                explicit != null -> explicit
                label != null -> resolveByLabel(pm, label)
                    ?: return@withContext ActionResult.error("no installed app matches '$label'")
                else -> return@withContext ActionResult.error("package or name is required")
            }
            val intent = pm.getLaunchIntentForPackage(pkg)
                ?: return@withContext ActionResult.error(
                    "no launchable app for '$pkg' (it may not be installed, or not visible to Jarvis)"
                )
            startActivity(ctx, intent, pkg)
        }

    internal fun resolveByLabel(pm: PackageManager, label: String): String? {
        val wanted = label.trim().lowercase()
        val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val candidates = runCatching { pm.queryIntentActivities(main, 0) }.getOrDefault(emptyList())
        var contains: String? = null
        for (info in candidates) {
            val name = runCatching { info.loadLabel(pm).toString().lowercase() }.getOrDefault("")
            val pkg = info.activityInfo?.packageName ?: continue
            if (name == wanted) return pkg
            if (contains == null && (name.contains(wanted) || pkg.lowercase().contains(wanted))) {
                contains = pkg
            }
        }
        return contains
    }
}

/** Tier 1 — opening a page only shows it; anything that submits a form is Tier 3 UI automation. */
object OpenUrl : JarvisAction {
    override val id = "open_url"
    override val tier = ActionTier.AUTO
    override val description = "Open an http(s) URL in the default browser."
    override val paramsSchema = mapOf("url" to "string: http:// or https:// URL")
    override val capability = "apps"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val url = params.str("url") ?: return ActionResult.error("url is required")
        // Only ever http(s). `intent:`, `file:`, `content:` and friends can
        // launch arbitrary components or leak private files, so they are out —
        // this is a scheme allowlist, not a blocklist.
        val scheme = SsrfGuard.check(url).scheme
        if (scheme != "http" && scheme != "https") {
            return ActionResult.error("only http and https URLs can be opened")
        }
        return startActivity(ctx, Intent(Intent.ACTION_VIEW, Uri.parse(url)), url)
    }
}

/** Tier 2 — puts a chooser in front of the user; harmless but visible. */
object ShareText : JarvisAction {
    override val id = "share_text"
    override val tier = ActionTier.NOTIFY
    override val description = "Open the Android share sheet with some text."
    override val paramsSchema = mapOf(
        "text" to "string: the text to share",
        "subject" to "string (optional): subject line for apps that use one"
    )
    override val capability = "apps"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val text = params.str("text") ?: return ActionResult.error("text is required")
        val send = Intent(Intent.ACTION_SEND)
            .setType("text/plain")
            .putExtra(Intent.EXTRA_TEXT, text)
        params.str("subject")?.let { send.putExtra(Intent.EXTRA_SUBJECT, it) }
        return startActivity(ctx, Intent.createChooser(send, null), "share sheet")
    }
}

/** Tier 2 — the brief lists "start navigation" as Tier 2. */
object StartNavigation : JarvisAction {
    override val id = "start_navigation"
    override val tier = ActionTier.NOTIFY
    override val description = "Open navigation to a destination in the installed maps app."
    override val paramsSchema = mapOf(
        "destination" to "string: address or place name",
        "latitude" to "double (optional): use with longitude instead of destination",
        "longitude" to "double (optional)"
    )
    override val capability = "apps"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val lat = if (params.has("latitude")) params.optDouble("latitude") else null
        val lon = if (params.has("longitude")) params.optDouble("longitude") else null
        val destination = params.str("destination")
        // geo: is the open, un-Googled scheme — OsmAnd, Organic Maps and
        // anything else that registers it will take the hand-off.
        val uri = when {
            lat != null && lon != null && !lat.isNaN() && !lon.isNaN() ->
                Uri.parse("geo:$lat,$lon?q=$lat,$lon" + (destination?.let { "(${Uri.encode(it)})" } ?: ""))
            destination != null -> Uri.parse("geo:0,0?q=${Uri.encode(destination)}")
            else -> return ActionResult.error("destination, or latitude and longitude, is required")
        }
        return startActivity(ctx, Intent(Intent.ACTION_VIEW, uri), "navigation to ${destination ?: "$lat,$lon"}")
    }
}

/**
 * Tier 3 — the brief puts `dial` at Tier 3 even though ACTION_DIAL only
 * pre-fills the dialer: anything that reaches another person confirms first.
 */
object DialNumber : JarvisAction {
    override val id = "dial"
    override val tier = ActionTier.CONFIRM
    override val description = "Open the phone dialer with a number pre-filled (does NOT call)."
    override val paramsSchema = mapOf("number" to "string: phone number")
    override val capability = "telephony"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val number = params.str("number") ?: return ActionResult.error("number is required")
        if (!PhoneNumbers.isPlausible(number)) {
            return ActionResult.error("'$number' does not look like a phone number")
        }
        return startActivity(ctx, Intent(Intent.ACTION_DIAL, Uri.parse("tel:${Uri.encode(number)}")), "dialer for $number")
    }
}

/**
 * Tier 1 — opening a settings screen changes nothing by itself. This is the
 * sanctioned replacement for the toggles Android no longer lets apps flip
 * (wifi, bluetooth, mobile data): put the switch in front of the human.
 */
object OpenSettingsPanel : JarvisAction {
    override val id = "open_settings_panel"
    override val tier = ActionTier.AUTO
    override val description =
        "Open a system settings screen or quick panel so the user can change something Android does not let apps change."
    override val paramsSchema = mapOf(
        "panel" to "string: internet | wifi | bluetooth | nfc | volume | location | display | sound | " +
            "battery | apps | app_info | accessibility | dnd_access | write_settings | airplane | " +
            "data_usage | vpn | date | developer | notification_access"
    )
    override val capability = "device_settings"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val panel = (params.str("panel") ?: return ActionResult.error("panel is required")).lowercase()
        val intent = when (panel) {
            "internet", "mobile_data", "data" -> Intent(Settings.Panel.ACTION_INTERNET_CONNECTIVITY)
            "wifi" -> Intent(Settings.Panel.ACTION_WIFI)
            "wifi_settings" -> Intent(Settings.ACTION_WIFI_SETTINGS)
            "nfc" -> Intent(Settings.Panel.ACTION_NFC)
            "volume" -> Intent(Settings.Panel.ACTION_VOLUME)
            "bluetooth" -> Intent(Settings.ACTION_BLUETOOTH_SETTINGS)
            "location" -> Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS)
            "display", "brightness" -> Intent(Settings.ACTION_DISPLAY_SETTINGS)
            "sound" -> Intent(Settings.ACTION_SOUND_SETTINGS)
            "battery" -> Intent(Settings.ACTION_BATTERY_SAVER_SETTINGS)
            "apps" -> Intent(Settings.ACTION_APPLICATION_SETTINGS)
            "app_info" -> Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(Uri.fromParts("package", params.str("package") ?: ctx.packageName, null))
            "accessibility" -> Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
            "dnd_access" -> Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)
            "notification_access" -> Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
            "write_settings" -> Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS)
                .setData(Uri.fromParts("package", ctx.packageName, null))
            "airplane" -> Intent(Settings.ACTION_AIRPLANE_MODE_SETTINGS)
            "data_usage" -> Intent(Settings.ACTION_DATA_USAGE_SETTINGS)
            "vpn" -> Intent(Settings.ACTION_VPN_SETTINGS)
            "date", "time" -> Intent(Settings.ACTION_DATE_SETTINGS)
            "developer" -> Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
            else -> return ActionResult.error("unknown panel '$panel'")
        }
        return startActivity(ctx, intent, "settings: $panel")
    }
}

/** Tier 1 — read-only inventory. */
object ListInstalledApps : JarvisAction {
    override val id = "list_installed_apps"
    override val tier = ActionTier.AUTO
    override val description = "List launchable apps installed on this phone."
    override val paramsSchema = mapOf(
        "query" to "string (optional): only apps whose name or package contains this",
        "limit" to "int: maximum results (default 100)"
    )
    override val capability = "apps"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            val pm = ctx.packageManager
            val query = params.str("query")?.lowercase()
            val limit = params.optInt("limit", 100).coerceIn(1, 500)
            val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            val resolved = runCatching { pm.queryIntentActivities(main, 0) }.getOrDefault(emptyList())
            val apps = JSONArray()
            var count = 0
            val seen = HashSet<String>()
            for (info in resolved) {
                val pkg = info.activityInfo?.packageName ?: continue
                if (!seen.add(pkg)) continue
                val label = runCatching { info.loadLabel(pm).toString() }.getOrDefault(pkg)
                if (query != null && !label.lowercase().contains(query) && !pkg.lowercase().contains(query)) {
                    continue
                }
                apps.put(json("package" to pkg, "name" to label))
                if (++count >= limit) break
            }
            ActionResult.ok(
                json(
                    "apps" to apps,
                    "count" to count,
                    "note" to "only apps visible to Jarvis under Android 11+ package visibility rules"
                )
            )
        }
}

/**
 * Declared purely so the model gets a clear "no" instead of hallucinating a
 * workaround. Short-circuited before policy, so it never prompts.
 */
object KillApp : JarvisAction {
    override val id = "kill_app"
    override val tier = ActionTier.CONFIRM
    override val description = "(Not possible on modern Android.) Force-stop another app."
    override val paramsSchema = mapOf("package" to "string: package id")
    override val capability = "apps"
    override val unsupported = true
    override val unsupportedReason =
        "Android does not let one app force-stop another. Use open_settings_panel with " +
            "panel=app_info so the user can tap Force stop, or run_shell (Shizuku) with " +
            "'am force-stop <package>' if Shizuku is running."

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        ActionResult.unsupported(unsupportedReason!!)
}
