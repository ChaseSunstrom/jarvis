package ai.jarvis.app.compat

import android.Manifest
import android.content.Context
import android.os.Build

/**
 * Every dangerous permission Jarvis declares, and what it is for.
 *
 * ## Why this file exists
 *
 * The manifest says, at the top, that "every dangerous permission is requested
 * at runtime, at the moment it is first needed". That was not true. Every
 * `requestPermissions` call in the app asked for `RECORD_AUDIO` or
 * `POST_NOTIFICATIONS`; the other eleven were declared and never asked for. A
 * declared runtime permission nobody requests is a permission you do not have,
 * so `send_sms`, `place_call`, `read_contacts`, `read_calendar`,
 * `create_calendar_event`, `get_location` and `get_sensors` returned
 * `permission … not granted` on every device, for the whole life of the app,
 * with no dialog ever appearing and nothing in the UI saying why.
 *
 * It was invisible from every direction. The action code was right — each one
 * re-checks its permission and returns an honest error. The manifest was right
 * — every permission was declared. SYSTEM CHECK said "Everything is granted",
 * because it only listed the grants it knew to ask about. The gap was between
 * them, and nothing owned it.
 *
 * So this is the list, in one place, and it is the input to three things:
 *
 *  1. [ai.jarvis.app.automation.actions.ActionRegistry] — the dispatcher asks
 *     for what an action needs, at the moment it is needed, which is what the
 *     manifest promised.
 *  2. [GrapheneCompat.evaluate] — every entry here belongs to exactly one row
 *     of the SYSTEM CHECK checklist, so "everything is granted" means it.
 *  3. `tools/runtime_permissions_test.py` — which fails the build if a
 *     dangerous permission appears in the manifest and not here, or here and
 *     not in the manifest. That is the check that was missing.
 *
 * ## What is deliberately NOT here
 *
 * **Special access** — `SYSTEM_ALERT_WINDOW`, `WRITE_SETTINGS`,
 * `SCHEDULE_EXACT_ALARM`, `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`,
 * `USE_FULL_SCREEN_INTENT` on 14+. None of them can be granted by
 * `requestPermissions`; each is a trip to a Settings screen, which
 * [GrapheneCompat.openSettingsFor] already owns. Putting one in this list would
 * produce a request that is denied instantly and forever, which is worse than
 * the Settings trip it replaced.
 *
 * **Normal permissions** — `INTERNET`, `VIBRATE`, `FLASHLIGHT`,
 * `ACCESS_NETWORK_STATE`, `com.android.alarm.permission.SET_ALARM` and the
 * rest. Granted at install; requesting them is a no-op that still costs a round
 * trip through an Activity.
 */
object RuntimePermissions {

    // ------------------------------------------------------------------
    // Checklist group ids
    //
    // These live here rather than in GrapheneCompat because this file is what
    // decides which permissions share a row. GrapheneCompat re-exports them so
    // the checklist reads the same as every other requirement id.
    // ------------------------------------------------------------------

    const val ID_PEOPLE = "people"
    const val ID_CALENDAR = "calendar"
    const val ID_LOCATION = "location"
    const val ID_MEDIA = "media"
    const val ID_SENSORS = "sensors"

    /** The API floor this app builds against; below it nothing here is runtime. */
    private const val MIN_SDK = 23

    /**
     * One dangerous permission.
     *
     * [group] is a [GrapheneCompat] requirement id, which is the invariant that
     * keeps the checklist honest: every permission in this table shows up on
     * exactly one row of SYSTEM CHECK, so a permission can never be missing
     * while that screen says everything is granted.
     */
    data class Entry(
        val permission: String,
        /** A checklist requirement id — one of the `ID_*` above, or in [GrapheneCompat]. */
        val group: String,
        /** What stops working without it. Concrete — it is shown to the user. */
        val why: String,
        /**
         * Below this API level the platform grants it at install time, so it is
         * not a runtime permission on this device and must not be requested:
         * asking for one the manifest does not declare *for this SDK* is an
         * immediate and permanent denial.
         */
        val minSdk: Int = MIN_SDK,
        /**
         * Above this API level the manifest declares it with a
         * `maxSdkVersion`, so it is not held, not requestable, and not needed —
         * `READ_EXTERNAL_STORAGE` on Android 13+, replaced by the `READ_MEDIA_*`
         * trio. Requesting a permission the manifest does not declare for the
         * running SDK is an instant, permanent denial.
         */
        val maxSdk: Int = Int.MAX_VALUE,
        /**
         * True for permissions the platform refuses to grant alongside their
         * foreground counterpart. Background location is the only one: Android
         * requires it to be requested **on its own, after** foreground location
         * is already held, and from Android 11 it cannot be granted from a
         * dialog at all — it is a Settings trip. Bundling it does not fail
         * loudly; it silently drops every permission in the request.
         */
        val separately: Boolean = false,
    )

    /**
     * The table. Order is the order a request bundles them in, which is the
     * order the OS shows the dialogs in, so related grants sit together.
     */
    @JvmField
    val ALL: List<Entry> = listOf(
        Entry(
            permission = Manifest.permission.RECORD_AUDIO,
            group = GrapheneCompat.ID_MICROPHONE,
            why = "Required to speak to Jarvis at all.",
        ),
        Entry(
            permission = "android.permission.POST_NOTIFICATIONS",
            group = GrapheneCompat.ID_POST_NOTIFICATIONS,
            why = "Without it Jarvis cannot show the listening notification, the " +
                "wake-word alert, or a Tier-3 approval — so approvals time out " +
                "unanswered.",
            minSdk = 33,
        ),
        Entry(
            permission = Manifest.permission.READ_CONTACTS,
            group = ID_PEOPLE,
            why = "\"Text Sam\" cannot become a phone number without it, and the " +
                "consent prompt would have to show you a name instead of the " +
                "number the message is actually going to.",
        ),
        Entry(
            permission = Manifest.permission.SEND_SMS,
            group = ID_PEOPLE,
            why = "Sending a message. Every send is Tier 3 and asks you first.",
        ),
        Entry(
            permission = Manifest.permission.CALL_PHONE,
            group = ID_PEOPLE,
            why = "Placing a call. Tier 3, every time.",
        ),
        Entry(
            permission = Manifest.permission.READ_CALENDAR,
            group = ID_CALENDAR,
            why = "\"What is on today\" reads nothing without it.",
        ),
        Entry(
            permission = Manifest.permission.WRITE_CALENDAR,
            group = ID_CALENDAR,
            why = "Creating an event.",
        ),
        Entry(
            permission = Manifest.permission.ACCESS_COARSE_LOCATION,
            group = ID_LOCATION,
            why = "\"Where am I\", \"is it far\", and weather for where you are.",
        ),
        Entry(
            permission = Manifest.permission.ACCESS_FINE_LOCATION,
            group = ID_LOCATION,
            why = "Navigation and location triggers need the precise fix.",
        ),
        Entry(
            permission = Manifest.permission.ACCESS_BACKGROUND_LOCATION,
            group = ID_LOCATION,
            why = "Location triggers while Jarvis is not on screen. Android grants " +
                "this only from its own Settings screen, never from a dialog.",
            minSdk = 29,
            separately = true,
        ),
        Entry(
            permission = Manifest.permission.CAMERA,
            group = ID_MEDIA,
            why = "\"What am I looking at\" — an explicit, user-visible capture.",
        ),
        Entry(
            permission = "android.permission.READ_MEDIA_IMAGES",
            group = ID_MEDIA,
            why = "Reading a photo you point Jarvis at.",
            minSdk = 33,
        ),
        Entry(
            permission = "android.permission.READ_MEDIA_AUDIO",
            group = ID_MEDIA,
            why = "Reading an audio file you point Jarvis at.",
            minSdk = 33,
        ),
        Entry(
            permission = Manifest.permission.READ_EXTERNAL_STORAGE,
            group = ID_MEDIA,
            why = "What the two above are called on Android 12 and earlier.",
            maxSdk = 32,
        ),
        Entry(
            permission = "android.permission.ACTIVITY_RECOGNITION",
            group = ID_SENSORS,
            why = "Step count. Without it get_sensors reports nothing walked.",
            minSdk = 29,
        ),
        Entry(
            permission = "android.permission.BLUETOOTH_CONNECT",
            group = ID_SENSORS,
            why = "Which car or headset you are connected to, which is what the " +
                "wake-word gate and the earpiece routing key off.",
            minSdk = 31,
        ),
    )

    /** Indexed by permission, for the dispatcher's per-action lookup. */
    private val BY_PERMISSION: Map<String, Entry> by lazy { ALL.associateBy { it.permission } }

    /** The entry for [permission], or null when it is not a runtime permission. */
    @JvmStatic
    fun entryOf(permission: String): Entry? = BY_PERMISSION[permission]

    /**
     * True when this app may put [permission] in front of the user in a
     * permission dialog *on this device*.
     *
     * Three ways to be false, and each one matters:
     *
     *  * it is not a dangerous permission at all (normal, or special access);
     *  * it is dangerous, but the running OS grants it at install because this
     *    device predates the release that made it runtime;
     *  * it is background location, which the platform will not grant from a
     *    dialog.
     */
    @JvmStatic
    fun isRequestable(permission: String): Boolean {
        val entry = BY_PERMISSION[permission] ?: return false
        return !entry.separately && appliesHere(entry)
    }

    /** True when this device is in the entry's declared API window. */
    private fun appliesHere(entry: Entry): Boolean =
        Build.VERSION.SDK_INT >= entry.minSdk && Build.VERSION.SDK_INT <= entry.maxSdk

    /**
     * True when this device already has [permission], or does not need it.
     *
     * "Does not need it" is the version gate: on an Android 12 phone
     * `POST_NOTIFICATIONS` is granted by being declared. Anything not in the
     * table answers by the ordinary check, so a caller can pass a special
     * access or a normal permission through here without a special case.
     */
    @JvmStatic
    fun isHeld(context: Context, permission: String): Boolean {
        val entry = BY_PERMISSION[permission]
        if (entry != null && !appliesHere(entry)) return true
        return GrapheneCompat.hasPermission(context, permission)
    }

    /**
     * The subset of [wanted] that this app could ask for and does not have.
     *
     * Deliberately NOT "everything missing": a permission that cannot be
     * requested — special access, background location, a normal permission the
     * caller passed by mistake — is left out, because a request containing one
     * is a request the platform drops on the floor. Those are reported by the
     * checklist and fixed in Settings.
     */
    @JvmStatic
    fun missing(context: Context, wanted: List<String>): List<String> =
        wanted.filter { isRequestable(it) && !isHeld(context, it) }

    /** Every permission in [group] that applies to this device. */
    @JvmStatic
    fun inGroup(group: String): List<String> =
        ALL.filter { it.group == group && appliesHere(it) }.map { it.permission }

    /**
     * Whether a whole checklist group is satisfied.
     *
     * Background location is excluded, deliberately: it is a Settings trip most
     * people will never make, and letting it hold the Location row red forever
     * would train the user to ignore the row.
     */
    @JvmStatic
    fun groupHeld(context: Context, group: String): Boolean =
        ALL.asSequence()
            .filter { it.group == group && !it.separately && appliesHere(it) }
            .all { isHeld(context, it.permission) }
}
