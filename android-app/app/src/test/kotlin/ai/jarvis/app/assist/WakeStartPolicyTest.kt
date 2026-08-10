package ai.jarvis.app.assist

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The rule behind "I have to open the app and start listening again".
 *
 * A foreground service typed `microphone` is a while-in-use service, and
 * Android will not let one start while the app is in the background.
 * `BOOT_COMPLETED` is an exemption from the general background-start
 * restriction and explicitly not one for the while-in-use types — so after
 * every reboot the wake listener was refused, the refusal was logged onto a
 * phone nobody has a cable for, and always-on listening was off until the app
 * was opened.
 *
 * Deciding that in advance is what turns the refusal into something the user
 * can see and fix, so the decision is a pure function and this is its table.
 */
class WakeStartPolicyTest {

    private fun route(
        enabled: Boolean = true,
        hasMic: Boolean = true,
        fromForeground: Boolean = false,
        sdkInt: Int = 35,
        doze: Boolean = false,
        overlays: Boolean = false,
    ) = WakeStartPolicy.route(
        enabled = enabled,
        hasMicPermission = hasMic,
        fromForeground = fromForeground,
        sdkInt = sdkInt,
        ignoringBatteryOptimizations = doze,
        canDrawOverlays = overlays,
    )

    @Test
    fun theSwitchIsTheSwitch() {
        // Nothing else is consulted: a user who turned it off gets silence even
        // with every exemption granted.
        assertEquals(
            WakeStartPolicy.Route.OFF,
            route(enabled = false, fromForeground = true, doze = true, overlays = true),
        )
    }

    @Test
    fun theMicrophonePermissionComesBeforeAnyStart() {
        // Starting without it would put up a notification saying "Jarvis is
        // listening" over a recorder that cannot open.
        assertEquals(
            WakeStartPolicy.Route.NEEDS_MIC_PERMISSION,
            route(hasMic = false, fromForeground = true, doze = true, overlays = true),
        )
    }

    @Test
    fun aResumedActivityIsAlwaysAllowed() {
        assertEquals(WakeStartPolicy.Route.DIRECT, route(fromForeground = true))
        assertEquals(
            WakeStartPolicy.Route.DIRECT,
            route(fromForeground = true, sdkInt = WakeStartPolicy.FIRST_RESTRICTED_SDK),
        )
    }

    @Test
    fun beforeAndroidTwelveThereIsNothingToRefuseTheStart() {
        for (sdk in 29 until WakeStartPolicy.FIRST_RESTRICTED_SDK) {
            assertEquals("sdk $sdk", WakeStartPolicy.Route.DIRECT, route(sdkInt = sdk))
        }
    }

    /** The regression: a boot receiver on a modern phone with neither exemption. */
    @Test
    fun aBackgroundStartOnTwelveOrLaterNeedsATap() {
        for (sdk in WakeStartPolicy.FIRST_RESTRICTED_SDK..36) {
            assertEquals("sdk $sdk", WakeStartPolicy.Route.NEEDS_A_TAP, route(sdkInt = sdk))
        }
    }

    @Test
    fun eitherDocumentedExemptionIsEnoughOnItsOwn() {
        assertEquals(WakeStartPolicy.Route.DIRECT, route(doze = true))
        assertEquals(WakeStartPolicy.Route.DIRECT, route(overlays = true))
        assertEquals(WakeStartPolicy.Route.DIRECT, route(doze = true, overlays = true))
    }

    @Test
    fun everyRefusalHasSomethingToSay() {
        // The bug this exists to fix was a refusal that produced no sentence.
        assertNotNull(WakeStartPolicy.explain(WakeStartPolicy.Route.NEEDS_A_TAP))
        assertNotNull(WakeStartPolicy.explain(WakeStartPolicy.Route.NEEDS_MIC_PERMISSION))
        // And the two that are not refusals stay quiet, or the user gets a
        // notification every quarter of an hour saying everything is fine.
        assertNull(WakeStartPolicy.explain(WakeStartPolicy.Route.OFF))
        assertNull(WakeStartPolicy.explain(WakeStartPolicy.Route.DIRECT))
    }

    @Test
    fun theTwoRefusalsDoNotSayTheSameThing() {
        // They are fixed in different places — a permission dialog and a
        // Settings toggle — so a shared sentence would send half the users to
        // the wrong screen.
        assertEquals(
            2,
            setOf(
                WakeStartPolicy.explain(WakeStartPolicy.Route.NEEDS_A_TAP),
                WakeStartPolicy.explain(WakeStartPolicy.Route.NEEDS_MIC_PERMISSION),
            ).size,
        )
    }
}
