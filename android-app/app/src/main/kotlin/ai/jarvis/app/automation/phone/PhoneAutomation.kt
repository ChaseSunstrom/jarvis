package ai.jarvis.app.automation.phone

import ai.jarvis.app.BuildConfig

/**
 * Driving THIS phone: reading the screen of whatever app is in front, reading
 * notifications, tapping things on the user's behalf.
 *
 * **It is off.** `BuildConfig.PHONE_AUTOMATION` is `false` in every build this
 * project produces, the two Android services that would feed it refuse to do
 * anything while it is, and [PhoneAutomation.available] is the one thing any
 * caller may ask. The interface exists so the shape is designed rather than
 * improvised in a hurry later; nothing behind it runs.
 *
 * ## Why a flag and not simply "later"
 *
 * An assistant that can read every screen on a phone is a different product
 * from one that turns the lights off, with a different threat model:
 *
 * * an accessibility service sees **everything** — banking apps, messages, the
 *   password manager's autofill — and Android gives it no way to be selective;
 * * a notification listener sees message content from apps that never intended
 *   Jarvis to have it;
 * * an injected tap is indistinguishable, to the app receiving it, from the
 *   user's own finger.
 *
 * None of that is made safe by care in this file. It is made *decidable* by
 * being off until somebody turns it on knowing all three, which is what the
 * flag is: `-PphoneAutomation=true`, deliberately awkward, and
 * `android-app/docs/phone-automation.md` next to it.
 *
 * ## What it is NOT
 *
 * This is not the home automation layer. `AutomationBridge` and everything
 * under `automation/actions/` — turning a light off, sending a message with
 * approval, reading a sensor — are the *house*, they are shipped, and they are
 * governed by the tier system. This interface is about the phone itself.
 */
interface PhoneAutomation {

    /** What this implementation can do, as action ids the bridge would expose. */
    fun capabilities(): List<String>

    /**
     * Read the screen in front of the user, as a description a model could use.
     *
     * Returns null when the accessibility service is not connected, which is
     * always in a shipped build.
     */
    suspend fun readScreen(): ScreenSnapshot?

    /**
     * Perform one interaction — a tap, a scroll, typing into a field.
     *
     * Every call is a Tier-3 action in the house's tier table: it happens on
     * the user's own phone, to an app that did not consent to it, and there is
     * no undo. An implementation that took a shortcut around the consent
     * prompt would be the largest hole in this project.
     */
    suspend fun act(request: Interaction): Outcome

    /** One frame of what is on screen. Deliberately small. */
    data class ScreenSnapshot(
        /** The package in front, e.g. `com.android.settings`. */
        val packageName: String,
        /** The window's title, when it has one. */
        val title: String,
        /** Text nodes, in reading order, already stripped of password fields. */
        val text: List<String>,
    )

    /** One thing to do to the screen. */
    data class Interaction(
        /** `tap` · `scroll` · `type` · `back` · `home`. */
        val kind: String,
        /** What to act on, as the accessibility node's text or id. */
        val target: String = "",
        /** For `type`. Never a password: the caller cannot know it is not one. */
        val text: String = "",
    )

    /** What happened, in terms a model can report without embellishing. */
    data class Outcome(val ok: Boolean, val detail: String = "")

    companion object {

        /**
         * The only honest answer while the flag is off, and the only thing
         * anything in the app is allowed to ask.
         *
         * A caller that branches on this gets a straight `false` in every
         * shipped build, so the whole feature is dead code the compiler can
         * see — which is the difference between "we decided not to ship it"
         * and "it is in there somewhere".
         */
        val available: Boolean get() = BuildConfig.PHONE_AUTOMATION

        /**
         * The implementation, or null.
         *
         * Null in every shipped build. Wiring one in is a deliberate act: set
         * this from the accessibility service's `onServiceConnected` **after**
         * checking [available], and nowhere else.
         */
        @Volatile
        var delegate: PhoneAutomation? = null
            get() = if (available) field else null

        /** For a test that wants the field back the way it found it. */
        fun clearForTest() {
            delegate = null
        }
    }
}
