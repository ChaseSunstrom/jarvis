package ai.jarvis.app.support

import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import org.junit.Assert.fail
import java.io.ByteArrayOutputStream

/**
 * The handful of things a test has to arrange on the device itself: an awake and
 * unlocked screen, runtime permissions, and the system animation scales.
 *
 * Everything goes through `UiDevice.executeShellCommand`, which runs as the
 * shell user with the instrumentation's privileges — the same thing a human
 * would type over adb, so nothing here can do something a developer at a
 * terminal could not.
 */
object Device {

    private const val TAG = "JarvisTestDevice"

    val ui: UiDevice
        get() = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())

    val packageName: String
        get() = InstrumentationRegistry.getInstrumentation().targetContext.packageName

    /** Run a shell command, returning its output (empty string on failure). */
    fun shell(command: String): String = try {
        ui.executeShellCommand(command)
    } catch (t: Throwable) {
        Log.w(TAG, "shell failed: $command", t)
        ""
    }

    /**
     * Screen on and past the keyguard.
     *
     * Both halves matter. `ApprovalActivity` and `CompanionAskActivity` render
     * over the keyguard on purpose but keep their controls inert behind it —
     * that is the security property, not an inconvenience — so a test that
     * needs to read parameters or tap an option needs a genuinely unlocked
     * phone. `wm dismiss-keyguard` is the emulator-friendly way to get one;
     * a device with a real credential would need it typed, which is why this
     * suite is documented as emulator-only.
     */
    fun wakeAndUnlock() {
        val device = ui
        try {
            if (!device.isScreenOn) device.wakeUp()
        } catch (t: Throwable) {
            Log.w(TAG, "wakeUp failed", t)
        }
        shell("input keyevent KEYCODE_WAKEUP")
        shell("wm dismiss-keyguard")
        device.waitForIdle(IDLE_MS)
    }

    /**
     * Grant runtime permissions the app would otherwise have to ask for.
     *
     * Not a bypass of anything Jarvis enforces: these are Android's own runtime
     * permissions, which decide whether an action is *possible*, and are a
     * strictly separate axis from the Tier-1/2/3 policy that decides whether it
     * is *allowed*. `docs/actions.md` and the manifest both say so. Granting
     * RECORD_AUDIO does not make a Tier-3 action skip its consent prompt.
     *
     * Unknown or not-yet-existing permissions (POST_NOTIFICATIONS below API 33)
     * simply fail in the shell and are ignored.
     */
    fun grant(vararg permissions: String) {
        for (permission in permissions) {
            val output = shell("pm grant $packageName $permission")
            if (output.isNotBlank()) Log.i(TAG, "pm grant $permission: ${output.trim()}")
        }
    }

    /**
     * Is [permission] actually held right now?
     *
     * `pm grant` fails quietly on an image that does not know a permission, and
     * [grant] deliberately swallows that — most tests do not care. The ones that
     * DO care should say so before they start, because "RECORD_AUDIO was never
     * granted" surfaces ninety seconds later as "no transcript ever rendered",
     * which sends the reader off debugging a server.
     */
    fun isGranted(permission: String): Boolean =
        InstrumentationRegistry.getInstrumentation().targetContext
            .checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    /** Fail now, with the reason, rather than as a timeout somewhere else. */
    fun requireGranted(permission: String) {
        if (isGranted(permission)) return
        fail(
            "$permission is not granted to $packageName, and this test cannot " +
                "work without it. JarvisTestRule runs `pm grant`; on this image " +
                "it did not take. Grant it by hand:\n" +
                "  adb shell pm grant $packageName $permission"
        )
    }

    /** The runtime permissions every instrumented test in this suite wants. */
    fun grantStandardTestPermissions() {
        val wanted = mutableListOf(
            "android.permission.RECORD_AUDIO",
            "android.permission.ACCESS_COARSE_LOCATION",
        )
        if (Build.VERSION.SDK_INT >= 33) {
            wanted += "android.permission.POST_NOTIFICATIONS"
        }
        grant(*wanted.toTypedArray())
    }

    // --- animation scales ---------------------------------------------------

    /**
     * The three global animation scales, as strings, so they can be restored
     * exactly as they were found.
     *
     * `testOptions { animationsDisabled = true }` sets all three to 0 for the
     * whole instrumented run. That is right for every test except the one that
     * is about an animation: `JarvisBootAnimation` reads
     * `Settings.Global.ANIMATOR_DURATION_SCALE` and correctly collapses to its
     * end state at 0, so BootAnimationTest has to put them back for its own
     * duration and then hand them over as it found them.
     */
    data class AnimationScales(
        val window: String,
        val transition: String,
        val animator: String,
    )

    fun animationScales(): AnimationScales = AnimationScales(
        window = readScale("window_animation_scale"),
        transition = readScale("transition_animation_scale"),
        animator = readScale("animator_duration_scale"),
    )

    fun setAnimationScales(scales: AnimationScales) {
        writeScale("window_animation_scale", scales.window)
        writeScale("transition_animation_scale", scales.transition)
        writeScale("animator_duration_scale", scales.animator)
    }

    fun setAllAnimationScales(value: String) {
        setAnimationScales(AnimationScales(value, value, value))
    }

    private fun readScale(key: String): String {
        val raw = shell("settings get global $key").trim()
        // An unset scale reads back as the literal "null"; the platform default
        // for all three is 1.0, so that is what "as I found it" means.
        return if (raw.isEmpty() || raw == "null") "1.0" else raw
    }

    private fun writeScale(key: String, value: String) {
        shell("settings put global $key $value")
    }

    // --- foreground ---------------------------------------------------------

    /**
     * The package whose window is on top.
     *
     * For the one question the in-process lifecycle registry cannot answer:
     * "did tapping that button take us out of the app entirely?" Which of OUR
     * activities is showing is a question for `Activities.isResumed`, which is
     * exact and needs no output parsing.
     */
    fun foregroundPackage(): String? = try {
        ui.currentPackageName
    } catch (t: Throwable) {
        Log.w(TAG, "could not read the foreground package", t)
        null
    }

    /** True while this app owns the foreground window. */
    fun isJarvisForeground(): Boolean = foregroundPackage() == packageName

    /**
     * Best-effort dump of the current window hierarchy, for a failure message.
     *
     * An EMPTY dump is reported as empty rather than as nothing. Run
     * 31610213287 is the worked example: `ConsentGateTest` failed with
     * "ApprovalActivity did not start within 45000ms", printed
     * "Foreground window dump:" and then a blank line, and the one piece of
     * context the assertion carries said nothing at all — while the screenshot
     * that was supposed to cover for it also came back empty. Two diagnostics,
     * both silent, is how a failure becomes a guess.
     *
     * So a blank dump now says so, and says what little is knowable without it:
     * which package owns the foreground window, which is usually enough to tell
     * "the prompt never started" from "the whole UI was wedged".
     */
    fun windowDump(): String {
        val dumped = try {
            val out = ByteArrayOutputStream()
            ui.dumpWindowHierarchy(out)
            out.toString(Charsets.UTF_8.name())
        } catch (t: Throwable) {
            return "(window hierarchy unavailable: ${t.javaClass.simpleName}: ${t.message})"
        }
        if (dumped.isNotBlank()) return dumped
        val front = runCatching { ui.currentPackageName }.getOrNull()
        return "(window hierarchy came back EMPTY — uiautomator could not read the " +
            "screen. Foreground package: ${front ?: "unknown"}. An empty dump " +
            "usually means the system UI was busy or the device was not showing " +
            "anything this instrumentation could see.)"
    }

    private const val IDLE_MS = 3_000L
}
