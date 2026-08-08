package ai.jarvis.app.automation.policy

/**
 * PURE LOGIC — no Android imports, no org.json. Unit-testable on a plain JVM.
 *
 * The vocabulary of the device-side policy model. Everything here is
 * deliberately dumb data so that the decision in [PolicyEngine] can be read,
 * reviewed and mirrored in another language (see
 * `android-app/tools/policy_truth_table_test.py`, which is the executable spec).
 */

/**
 * How dangerous an action is. Ordinal order IS the severity order — AUTO is the
 * least dangerous, CONFIRM the most. Never reorder these constants.
 *
 *  * [AUTO]    — read-only or trivially reversible. Runs without asking.
 *  * [NOTIFY]  — changes device state but is recoverable. Ask once, then the
 *               user may choose to remember the answer for that action.
 *  * [CONFIRM] — irreversible, contacts another person, spends money, or types
 *               into someone else's UI. Asks EVERY time. Can never be
 *               remembered, can never be auto-approved.
 */
enum class ActionTier {
    AUTO,
    NOTIFY,
    CONFIRM;

    /** Wire form used by jarvis-core's `device_command.tier` field: 1 | 2 | 3. */
    val wire: Int get() = ordinal + 1

    companion object {
        /** The more dangerous of the two. Used to RAISE a tier, never to lower it. */
        fun max(a: ActionTier, b: ActionTier): ActionTier = if (a.ordinal >= b.ordinal) a else b

        /**
         * Parse the server's `tier` field. Returns null for anything we do not
         * recognise, and the caller then treats it as "no opinion" (= [AUTO],
         * i.e. no raise). A malformed or hostile value can therefore never
         * *lower* the local tier — see [PolicyEngine.effectiveTier].
         */
        fun fromWire(value: Int?): ActionTier? = when (value) {
            1 -> AUTO
            2 -> NOTIFY
            3 -> CONFIRM
            else -> null
        }

        /** Lenient name parse for stored prefs / manifests. Null when unknown. */
        fun fromName(name: String?): ActionTier? = when (name?.trim()?.uppercase()) {
            "AUTO", "1", "TIER1" -> AUTO
            "NOTIFY", "2", "TIER2" -> NOTIFY
            "CONFIRM", "3", "TIER3" -> CONFIRM
            else -> null
        }
    }
}

/**
 * The user's standing answer for one action id. `NEVER` is a hard local kill
 * switch and outranks everything, including the server and including
 * [UserPolicy.ALLOW_ALWAYS] set earlier.
 */
enum class UserPolicy {
    /** "Yes, and stop asking." Only honoured for [ActionTier.AUTO]/[ActionTier.NOTIFY]. */
    ALLOW_ALWAYS,

    /** Default for everything the user has not answered yet. */
    ASK,

    /** Hard no. Always denied, never prompts, never executes. */
    NEVER;

    companion object {
        /** Unknown / corrupt stored values fail closed to [ASK]. */
        fun fromStored(value: String?): UserPolicy = when (value?.trim()?.uppercase()) {
            "ALLOW_ALWAYS", "ALLOW", "ALWAYS" -> ALLOW_ALWAYS
            "NEVER", "DENY", "BLOCK" -> NEVER
            else -> ASK
        }
    }
}

/** The only three things the policy engine can say. */
enum class Decision {
    /** Execute now, no human in the loop. */
    ALLOW,

    /** Show the consent prompt with the verbatim action, params and reason. */
    ASK,

    /** Do not execute. Do not prompt. Reply `denied`. */
    DENY
}

/**
 * Where the request came from. This is NOT the server's word for it — the
 * caller sets it structurally:
 *
 *  * [TRUSTED]   — a `device_command` off the authenticated jarvis-core socket,
 *                  or a local UI tap by the user.
 *  * [UNTRUSTED] — anything whose content originated in a web page, a
 *                  notification, an OCR/screen read, a clipboard, or an HTTP
 *                  response. Text like that is DATA and must never be able to
 *                  cause an action on its own, so an UNTRUSTED request can
 *                  never be auto-allowed: the best it can ever get is [Decision.ASK].
 */
enum class TrustLevel { TRUSTED, UNTRUSTED }
