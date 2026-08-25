package ai.jarvis.app.ui

/**
 * The console's sections, as the phone knows them.
 *
 * ## Why this exists
 *
 * *"can you make sure the mobile app isn't a different screen from the web
 * view? they should be the same but with mobile permission stuff etc. because
 * right now it feels weird that it's kind of similar but not really"*
 *
 * It was kind of similar but not really. The phone's home screen offered
 * MANAGE, AUTOMATIONS and SETTINGS: MANAGE opened the console's root, SETTINGS
 * opened a native screen about this phone, and AUTOMATIONS opened a native
 * screen listing the tasks *this phone* runs by itself — which is a different
 * thing from the house's automations that happens to share a word. Two of the
 * three buttons went somewhere the browser has no equivalent of, and the one
 * that did land in the console dropped you at its front door with no way to
 * reach the other four sections.
 *
 * So the phone speaks the console's own nav now, and this is that nav. One
 * table, mirrored from `jarvis-web/src/routes/+layout.svelte` and pinned
 * against it by `android-app/tools/console_parity_test.py`, because "the same
 * sections in the same order" is precisely the property that rots the moment a
 * page is added to one of the two.
 *
 * ## What stays native, and why
 *
 * Everything a web page in a WebView cannot do:
 *
 *  * the microphone, the wake word and the floating orb;
 *  * Android permissions, battery exemption, the model download;
 *  * which server this phone talks to, and the token it uses;
 *  * the tasks this phone runs on its own, with their consent switches.
 *
 * Those live behind one more entry — [PHONE] — which is deliberately NOT one of
 * the console's tabs. It is the mobile half, and calling it "Settings" next to
 * a tab already called SETTINGS is how the two got confused in the first place.
 *
 * ## Why a path is not a String parameter
 *
 * [ManagementActivity] loads this path into a WebView that carries the user's
 * bearer token. A caller-supplied URL would be a way for any component on the
 * device that can start an activity to point an authenticated session
 * anywhere. The intent carries the enum's NAME; the path comes from this table
 * and nowhere else.
 */
enum class ConsoleTab(
    /** What the button says, matching the console's own nav exactly. */
    val label: String,
    /** Path under the console's origin. Never taken from an intent. */
    val path: String,
) {
    DEVICES("DEVICES", "/devices"),
    AREAS("AREAS", "/areas"),
    AUTOMATIONS("AUTOMATIONS", "/automations"),
    TOOLS("TOOLS", "/tools"),
    TASKS("TASKS", "/tasks"),
    DASHBOARDS("DASHBOARDS", "/dashboards"),
    CODE("CODE", "/code"),
    NOTES("NOTES", "/notes"),
    MEMORY("MEMORY", "/memory"),
    // M07 put the console in a desktop window and gave it a page; the phone
    // gained nothing, so a browser at the same URL showed strictly more app
    // than the phone did. `console_parity_test.py` is the check that noticed.
    DESKTOP("DESKTOP", "/desktop"),
    SETTINGS("SETTINGS", "/settings");

    companion object {
        /** Where a tap with no tab lands. The console's own default. */
        val DEFAULT = DEVICES

        /**
         * Resolve an intent's extra, or [DEFAULT].
         *
         * Total by construction: an unknown name is a typo or a hostile caller,
         * and either way the answer is a tab from this table rather than an
         * exception on a screen the user just tapped a button to reach.
         */
        fun of(name: String?): ConsoleTab =
            entries.firstOrNull { it.name == name } ?: DEFAULT

        /**
         * The one entry that is not the console's.
         *
         * Named for the thing it is about, so it cannot be read as a duplicate
         * of [SETTINGS] — which is what "Settings" beside "SETTINGS" was.
         */
        const val PHONE_LABEL = "PHONE"
    }
}
