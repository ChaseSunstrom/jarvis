package ai.jarvis.app

import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import androidx.test.espresso.Espresso.closeSoftKeyboard
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.action.ViewActions.replaceText
import androidx.test.espresso.action.ViewActions.scrollTo
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.isAssignableFrom
import androidx.test.espresso.matcher.ViewMatchers.withHint
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import android.widget.EditText
import org.hamcrest.Matchers.allOf
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * What the user types into Settings is still there when they come back.
 *
 * Sounds trivial; it is the single most consequential piece of state in the app.
 * The server URL decides which host the phone will hold an authenticated socket
 * to, and the token is, in `JarvisConfig`'s own words, the key to the whole
 * house. A settings screen that appears to save and does not is an app that
 * silently does nothing forever, and no JVM test can tell the difference —
 * `SharedPreferences` is stubbed out there.
 *
 * ## Finding the fields
 *
 * The UI is built programmatically, so there are no resource ids. The stable
 * handle is the HINT, which is meaningful text chosen by the screen itself
 * (`http://192.168.2.10:8123`, `long-lived access token`) rather than a
 * positional index that quietly starts pointing at a different field the day
 * someone inserts a spacer.
 *
 * ## Two independent checks
 *
 * The values are asserted through `JarvisConfig` — the object every other part
 * of the app reads them through — AND by reopening the screen and reading them
 * off the fields. Either one alone can pass while the feature is broken: a
 * screen that writes correctly but populates its fields from somewhere else is
 * still wrong, and so is one whose fields look right because it never cleared
 * an in-memory copy.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class SettingsPersistenceTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun serverUrlAndTokenSurviveClosingAndReopeningTheScreen() {
        openSettings()

        typeInto(URL_HINT, SERVER_URL)
        typeInto(TOKEN_HINT, TOKEN)
        typeInto(PIPELINE_HINT, PIPELINE)
        typeInto(DEVICE_NAME_HINT, DEVICE_NAME)

        Screenshots.take("SettingsPersistenceTest-filled-in")

        save()

        // 1. Through the object the rest of the app reads.
        val config = JarvisConfig(context)
        assertEquals(
            "The server URL must be stored exactly as ServerUrl.check normalised it",
            SERVER_URL,
            config.serverUrl,
        )
        assertEquals("The access token must be stored verbatim", TOKEN, config.token)
        assertEquals("The pipeline name must be stored", PIPELINE, config.pipeline)
        assertEquals("The device name must be stored", DEVICE_NAME, config.deviceName)
        assertEquals(
            "A URL and a token together are the definition of configured",
            true,
            config.isConfigured,
        )

        // 2. Through the screen, reopened from scratch.
        openSettings()
        onField(URL_HINT).check(matches(withText(SERVER_URL)))
        onField(TOKEN_HINT).check(matches(withText(TOKEN)))
        onField(PIPELINE_HINT).check(matches(withText(PIPELINE)))
        onField(DEVICE_NAME_HINT).check(matches(withText(DEVICE_NAME)))

        Screenshots.take("SettingsPersistenceTest-reopened")
    }

    @Test
    fun anInvalidServerUrlIsRefusedAndNothingIsStored() {
        openSettings()

        // Cleartext to a public host. `ServerUrl.check` refuses it because the
        // platform's network security config would refuse the connection anyway,
        // and failing here says why. The important half of the assertion is that
        // the refusal does not half-save: a stored token with no usable URL is a
        // credential sitting on disk for no reason.
        typeInto(URL_HINT, "http://example.com")
        typeInto(TOKEN_HINT, TOKEN)
        tapSave()

        Waits.until("SAVE to leave the screen open after refusing the URL") {
            Activities.isResumed(SettingsActivity::class.java)
        }
        Screenshots.take("SettingsPersistenceTest-invalid-url")

        val config = JarvisConfig(context)
        assertEquals("A refused URL must not be stored", "", config.serverUrl)
        assertEquals("…and neither must the token that came with it", "", config.token)
        assertEquals("…so the app is still unconfigured", false, config.isConfigured)
    }

    @Test
    fun aMissingTokenIsRefused() {
        openSettings()

        typeInto(URL_HINT, SERVER_URL)
        typeInto(TOKEN_HINT, "")
        tapSave()

        Waits.until("SAVE to leave the screen open after refusing the empty token") {
            Activities.isResumed(SettingsActivity::class.java)
        }

        val config = JarvisConfig(context)
        assertEquals(
            "Without a token there is nothing to authenticate with, so nothing is saved",
            "",
            config.serverUrl,
        )
        assertEquals(false, config.isConfigured)

        Screenshots.take("SettingsPersistenceTest-missing-token")
    }

    @Test
    fun theDeviceIdIsStableAcrossScreenLaunches() {
        // Not cosmetic: `jarvis/device/register` identifies this install by it,
        // and one that changed between launches would look like a new device
        // needing fresh authorisation every time the app started.
        val first = JarvisConfig(context).deviceId
        openSettings()
        Activities.finishAll()
        openSettings()
        val second = JarvisConfig(context).deviceId

        assertEquals("The device id must be generated once and persisted", first, second)
        assertEquals(
            "…and it must be a real UUID, not an empty string standing in for one",
            UUID_LENGTH,
            first.length,
        )
    }

    // --- helpers ------------------------------------------------------------

    private fun openSettings() {
        val settings = Activities.launch(SettingsActivity::class.java)
        Activities.awaitResumed(settings)
        // Anchored on a label near the TOP of the screen. "SAVE" sits at the
        // bottom of a long ScrollView and would need scrolling to find, which
        // would leave the screen scrolled before anything had been typed.
        Waits.until("the settings screen to finish building") {
            Device.ui.findObject(By.text(Views.textIgnoringCase("Server URL"))) != null
        }
    }

    /** The one EditText carrying [hint]. */
    private fun onField(hint: String) =
        onView(allOf(isAssignableFrom(EditText::class.java), withHint(hint)))

    private fun typeInto(hint: String, text: String) {
        onField(hint).perform(scrollTo(), replaceText(text))
        closeSoftKeyboard()
    }

    /** Tap SAVE without asserting the outcome. SAVE is below the fold. */
    private fun tapSave() {
        val save = Views.findScrolling(By.text(Views.textIgnoringCase("SAVE")))
        requireNotNull(save) { "No SAVE button on screen.\n${Device.windowDump()}" }.click()
    }

    /** Tap SAVE and wait for the screen to close, which is what success looks like. */
    private fun save() {
        tapSave()
        Waits.until("SettingsActivity to finish after a successful SAVE") {
            !Activities.isResumed(SettingsActivity::class.java)
        }
    }

    private companion object {
        // Exactly the placeholder text `JarvisUi.field` was given for each row.
        const val URL_HINT = "http://192.168.2.10:8123"
        const val TOKEN_HINT = "long-lived access token"
        const val PIPELINE_HINT = JarvisConfig.DEFAULT_PIPELINE
        const val DEVICE_NAME_HINT = "This phone"

        /**
         * A private-range host with an explicit port: accepted by
         * `ServerUrl.check`, normalised to exactly this string (no trailing
         * slash), and it is the address `network_security_config.xml` documents.
         */
        const val SERVER_URL = "http://192.168.2.10:8123"
        const val TOKEN = "settings-persistence-test-token-4f2a"
        const val PIPELINE = "Jarvis Test Pipeline"
        const val DEVICE_NAME = "Instrumented Pixel"

        /** `UUID.randomUUID().toString()` is always 36 characters. */
        const val UUID_LENGTH = 36
    }
}
