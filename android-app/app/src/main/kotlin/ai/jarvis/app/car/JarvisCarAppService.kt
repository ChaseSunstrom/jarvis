package ai.jarvis.app.car

import android.content.Intent
import androidx.car.app.CarAppService
import androidx.car.app.Session
import androidx.car.app.Screen
import androidx.car.app.validation.HostValidator

/**
 * Jarvis on the car display.
 *
 * *"can you add complete functionality for android auto, with a view of Jarvis
 * on the display, similar to the web app view?"*
 *
 * ## What this is, and what it deliberately is not
 *
 * Android Auto apps do not draw. They describe a *template* — a pane, a list, a
 * message — and the host renders it in the car's own styling at the car's own
 * size. So "similar to the web app view" is the state, the words and the arc
 * reactor's identity rather than the HUD's literal canvas: [CarOrbRenderer]
 * draws the real orb into a bitmap so what appears is the same object, and
 * [JarvisCarScreen] puts the transcript and the reply beside it.
 *
 * The category is IOT, which is the one the platform has for controlling
 * things in a house, and is what Jarvis does. Claiming a navigation category
 * would buy the surface API — a real Canvas on the head unit — and would be a
 * lie to the host about what this app is; the driving-mode rules that come with
 * it exist for turn-by-turn guidance and nothing here is that.
 *
 * ## The microphone
 *
 * The car screen does not open one. The way in is the same as everywhere else
 * — say the name, and [ai.jarvis.app.assist.WakeWordService] hears it on the
 * phone in your pocket — plus an explicit control on the screen for a driver
 * who would rather press something. Nothing here starts listening because a
 * car connected; a surface that opened a microphone on pairing is a surface
 * nobody agreed to.
 *
 * ## Host validation
 *
 * `HostValidator.ALLOW_ALL_HOSTS_VALIDATOR` is exactly what its name says and
 * is NOT used, in any build type. Anything on the phone can try to bind an
 * exported service; the validator is what stands between "Android Auto is
 * connected" and "some other app is driving Jarvis's car surface", and a
 * debug-only hole is still a hole in the APK a developer is carrying around.
 *
 * `hosts_allowlist_sample` is misleadingly named — it is not a sample. The
 * array ships in `androidx.car.app` and holds the real signing-certificate
 * digests for `com.google.android.projection.gearhead` (Android Auto,
 * including the Desktop Head Unit) and for the Automotive templates host. It
 * is what Google's own documentation tells apps to pass, and checking a
 * digest against a package name is the actual mechanism.
 */
class JarvisCarAppService : CarAppService() {

    override fun createHostValidator(): HostValidator =
        HostValidator.Builder(applicationContext)
            .addAllowedHosts(androidx.car.app.R.array.hosts_allowlist_sample)
            .build()

    override fun onCreateSession(): Session = object : Session() {
        override fun onCreateScreen(intent: Intent): Screen = JarvisCarScreen(carContext)
    }
}
