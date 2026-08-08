package io.homeassistant.companion.android.jarvis

import android.content.Context

/**
 * Connection settings for the self-contained Jarvis assist client.
 *
 * The overlay talks DIRECTLY to Home Assistant's WebSocket API (same contract
 * as jarvis-web), so it needs the HA base URL and a long-lived access token.
 * Kept in its own SharedPreferences file, deliberately decoupled from the HA
 * companion app's internal auth/session storage. Enter them once in
 * [JarvisSettingsActivity]; use the WireGuard/LAN URL you reach HA on.
 */
class JarvisConfig(context: Context) {

    private val prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    var haUrl: String
        get() = prefs.getString(KEY_URL, "")!!.trimEnd('/')
        set(v) = prefs.edit().putString(KEY_URL, v.trim()).apply()

    var token: String
        get() = prefs.getString(KEY_TOKEN, "")!!
        set(v) = prefs.edit().putString(KEY_TOKEN, v.trim()).apply()

    var pipeline: String
        get() = prefs.getString(KEY_PIPELINE, "Jarvis")!!
        set(v) = prefs.edit().putString(KEY_PIPELINE, v.trim().ifEmpty { "Jarvis" }).apply()

    /** True once the minimum needed to connect is present. */
    val isConfigured: Boolean
        get() = haUrl.isNotEmpty() && token.isNotEmpty()

    companion object {
        private const val FILE = "jarvis_config"
        private const val KEY_URL = "ha_url"
        private const val KEY_TOKEN = "ha_token"
        private const val KEY_PIPELINE = "pipeline"
    }
}
