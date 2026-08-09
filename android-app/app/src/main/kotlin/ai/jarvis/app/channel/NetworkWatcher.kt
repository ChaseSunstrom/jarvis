package ai.jarvis.app.channel

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first

/**
 * "Is there a network at all?", so the reconnect loop can wait for one instead
 * of burning a wakelock rediscovering that the radio is off.
 *
 * Two things it deliberately does NOT do:
 *
 *  * It does not require `NET_CAPABILITY_VALIDATED`. That flag means "Android
 *    reached a captive-portal probe on the internet", and this app's entire
 *    point is talking to a box on your LAN. A home network with the WAN unplugged
 *    is unvalidated and perfectly usable for Jarvis.
 *  * It does not decide *which* network to use, or bind the socket to one. The
 *    server may be on Wi-Fi, on a WireGuard tunnel riding cellular, or on
 *    loopback in an emulator. Picking for the user would break at least one of
 *    those.
 *
 * `ConnectivityManager` only, no Play Services. Needs `ACCESS_NETWORK_STATE`,
 * which the manifest already declares.
 */
class NetworkWatcher(context: Context) {

    private val appContext = context.applicationContext
    private val manager: ConnectivityManager? =
        appContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager

    private val _online = MutableStateFlow(true)

    /** False only when we are confident there is no usable network. */
    val online: StateFlow<Boolean> get() = _online

    /**
     * Bumped every time a network appears. The reconnect loop watches it so a
     * Wi-Fi handover retries immediately instead of finishing a five-minute
     * backoff that started while the phone was in a lift.
     */
    private val _generation = MutableStateFlow(0)
    val generation: StateFlow<Int> get() = _generation

    private val available = HashSet<Network>()

    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            val first = synchronized(available) {
                available.add(network)
                available.size == 1
            }
            _online.value = true
            _generation.value = _generation.value + 1
            if (first) Log.i(TAG, "network available")
        }

        override fun onLost(network: Network) {
            val empty = synchronized(available) {
                available.remove(network)
                available.isEmpty()
            }
            if (empty) {
                _online.value = false
                Log.i(TAG, "no network")
            }
        }
    }

    private var registered = false

    fun start() {
        if (registered) return
        val cm = manager
        if (cm == null) {
            // No ConnectivityManager (shouldn't happen outside a broken mock):
            // assume online so the channel degrades to plain retry rather than
            // waiting forever for an event that will never arrive.
            Log.w(TAG, "no ConnectivityManager; assuming the network is up")
            _online.value = true
            return
        }
        try {
            // registerDefaultNetworkCallback: API 24+, and this app is minSdk 29.
            cm.registerDefaultNetworkCallback(callback)
            registered = true
        } catch (t: Throwable) {
            // A denied ACCESS_NETWORK_STATE or a vendor quirk must not stop the
            // channel — fail OPEN here. Failing closed on connectivity would
            // mean "no network monitoring" silently equals "no Jarvis".
            Log.w(TAG, "could not watch connectivity; assuming the network is up", t)
            _online.value = true
        }
    }

    fun stop() {
        if (!registered) return
        try {
            manager?.unregisterNetworkCallback(callback)
        } catch (t: Throwable) {
            Log.d(TAG, "unregister failed", t)
        }
        registered = false
        synchronized(available) { available.clear() }
    }

    /** Suspends until there is a network. Returns immediately when there is one. */
    suspend fun awaitOnline() {
        if (_online.value) return
        Log.i(TAG, "waiting for a network before reconnecting")
        _online.first { it }
    }

    companion object {
        private const val TAG = "JarvisNet"
    }
}
