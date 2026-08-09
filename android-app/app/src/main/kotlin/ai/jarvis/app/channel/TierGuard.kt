package ai.jarvis.app.channel

import java.util.Locale

/**
 * PURE LOGIC — no Android imports, no org.json. Mirrored by
 * `android-app/tools/channel_protocol_test.py`.
 *
 * The channel's own copy of the tier rule.
 *
 * `ai.jarvis.app.automation.policy.PolicyEngine` is the authority and does this
 * again, properly, against the real action table. This is the *first* of the
 * two gates and it exists because the two can fail independently: this one runs
 * on the socket thread with nothing but the registration manifest to go on, and
 * it is the layer that decides what tier string the dispatcher is even told
 * about.
 *
 * Two rules, and they are the whole file:
 *
 *  1. The `tier` field in an incoming `device_command` may only RAISE the tier.
 *     `effective = max(local, incoming)`. A server that says "this SMS is
 *     really a Tier 1" gets a Tier 3 anyway.
 *  2. An action the local manifest has never heard of is [WireTier.CONFIRM].
 *     Not "unknown", not "ask the server" — the most dangerous tier there is,
 *     so a typo or an injected action name cannot land in the auto-run bucket.
 *
 * There is deliberately no function here that lowers a tier, takes a "policy"
 * field off the wire, or accepts an override flag. There is no code path to
 * audit because there is no code.
 */
enum class WireTier(val wire: Int) {
    AUTO(1),
    NOTIFY(2),
    CONFIRM(3);

    /**
     * The string handed to `ActionDispatcher.dispatch`. Matches the enum names
     * of `automation.policy.ActionTier`, which `ActionTier.fromName` parses.
     */
    val label: String get() = name
}

object TierGuard {

    /**
     * Parse the wire `tier` field. Anything unrecognised — absent, null, a
     * string, 0, 4, 99 — is null, meaning "the server expressed no opinion".
     *
     * Null is safe precisely because [effective] folds it in as [WireTier.AUTO],
     * which cannot raise anything and cannot lower anything either. A hostile
     * value therefore has exactly two outcomes: raise the tier, or do nothing.
     */
    fun parse(value: Any?): WireTier? = when (value) {
        is Int -> fromWire(value)
        is Long -> fromWire(value.toInt())
        is Number -> fromWire(value.toInt())
        is String -> value.trim().toIntOrNull()?.let(::fromWire) ?: fromName(value)
        else -> null
    }

    fun fromWire(value: Int): WireTier? = when (value) {
        1 -> WireTier.AUTO
        2 -> WireTier.NOTIFY
        3 -> WireTier.CONFIRM
        else -> null
    }

    // Locale.ROOT: the device locale must not decide what a tier name means. In
    // a Turkish locale the default uppercase() maps 'i' to 'İ', so a server
    // sending "confirm" would parse to null and lose its RAISE.
    fun fromName(name: String?): WireTier? = when (name?.trim()?.uppercase(Locale.ROOT)) {
        "AUTO", "TIER1" -> WireTier.AUTO
        "NOTIFY", "TIER2" -> WireTier.NOTIFY
        "CONFIRM", "TIER3" -> WireTier.CONFIRM
        else -> null
    }

    /** The more dangerous of the two. The only combinator in this file. */
    fun max(a: WireTier, b: WireTier): WireTier = if (a.ordinal >= b.ordinal) a else b

    /**
     * `max(local, incoming)`. Incoming may raise; it can never lower.
     * A null incoming contributes [WireTier.AUTO] and changes nothing.
     */
    fun effective(local: WireTier, incoming: WireTier?): WireTier =
        max(local, incoming ?: WireTier.AUTO)

    /**
     * The tier for one command, given the action tiers this device advertised
     * at registration.
     *
     * [localTable] is built from the dispatcher's own manifest — the device's
     * word about its own actions — and never from anything the server sent.
     * An action missing from it is [WireTier.CONFIRM].
     */
    fun forAction(
        localTable: Map<String, WireTier>,
        actionId: String,
        incoming: WireTier?
    ): WireTier {
        val local = localTable[actionId] ?: WireTier.CONFIRM
        return effective(local, incoming)
    }

    /** True when the server tried to make an action *less* dangerous. Worth a log line. */
    fun isDowngradeAttempt(
        localTable: Map<String, WireTier>,
        actionId: String,
        incoming: WireTier?
    ): Boolean {
        val local = localTable[actionId] ?: WireTier.CONFIRM
        return incoming != null && incoming.ordinal < local.ordinal
    }
}
