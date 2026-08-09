package ai.jarvis.app.automation.accessibility

/**
 * PURE LOGIC — no Android imports.
 *
 * Apps the UI automation layer will not touch, no matter who asks.
 *
 * Tier 3 already means a human sees every tap before it happens, and that is
 * the real control. This is the second one: a place where a whole class of
 * targets is off the table before a prompt is even drawn, because "the user
 * tapped APPROVE" is a much weaker signal when the prompt is the fourteenth of
 * the evening and the target is their bank.
 *
 * The list is deliberately blunt:
 *
 *  * **Jarvis itself.** A model that can drive the Jarvis UI can flip its own
 *    policy switches, clear its own audit log and approve its own prompts. That
 *    is the entire safety model handed to the thing it constrains.
 *  * **Banking, payments, brokerage, crypto.** Money moves and does not move
 *    back.
 *  * **Password managers and authenticators.** One tap away from every other
 *    account the user owns.
 *  * **Security surfaces of Settings and the keyguard.** Where a device gets
 *    quietly re-owned: screen lock, biometrics, device admin, developer
 *    options, credential storage.
 *
 * Built-in entries cannot be removed — not by the user, not by the server, not
 * by a config file. The user may only ADD. That asymmetry is on purpose: an
 * "unblock my bank" flow is exactly what an attacker would try to talk somebody
 * into, and there is no legitimate automation that needs it badly enough to
 * justify the mechanism existing.
 *
 * Mirrored by `android-app/tools/screen_prune_test.py`.
 */
class PackageDenylist(
    /** Extra package ids or `prefix.*` patterns the user has added locally. */
    val userAdditions: Set<String> = emptySet(),
    /**
     * Token matching on package segments (`*bank*`, `*wallet*`, …). Catches the
     * ten thousand banks nobody can enumerate. On by default; a false positive
     * costs one refused automation and is listed in the refusal message.
     */
    val heuristicsEnabled: Boolean = true
) {

    /** Additive only — see the class doc for why there is no removal. */
    fun withUserAddition(pattern: String): PackageDenylist =
        PackageDenylist(userAdditions + normalize(pattern), heuristicsEnabled)

    fun withUserAdditions(patterns: Collection<String>): PackageDenylist =
        PackageDenylist(userAdditions + patterns.map(::normalize), heuristicsEnabled)

    /**
     * The gate every acting operation goes through, before any prompt.
     *
     * @param packageName foreground package. Blank/unknown BLOCKS — if we cannot
     *   tell what we are about to drive, we do not drive it.
     * @param windowClass the current activity or window class name, when known.
     *   Used for the Settings/keyguard rules, which are per-screen rather than
     *   per-app.
     */
    fun check(packageName: String?, windowClass: String? = null): DenylistVerdict {
        val pkg = normalize(packageName)
        if (pkg.isEmpty()) {
            return DenylistVerdict.blocked(
                rule = "unknown-foreground",
                target = "(unknown)",
                detail = "the foreground app could not be identified"
            )
        }

        strongWindowRule(windowClass)?.let {
            return DenylistVerdict.blocked("secure-window", pkg, it)
        }

        if (pkg == SELF_PACKAGE || pkg.startsWith("$SELF_PACKAGE.")) {
            return DenylistVerdict.blocked(
                "self", pkg,
                "Jarvis does not drive its own UI; that would let it approve its own prompts"
            )
        }

        if (pkg in PACKAGES) {
            return DenylistVerdict.blocked("builtin-package", pkg, "on the built-in denylist")
        }

        PREFIXES.firstOrNull { pkg == it.trimEnd('.') || pkg.startsWith(it) }?.let {
            return DenylistVerdict.blocked("builtin-prefix", pkg, "matches built-in prefix '$it'")
        }

        matchesUser(pkg)?.let {
            return DenylistVerdict.blocked("user-denylist", pkg, "matches your own entry '$it'")
        }

        if (pkg in WINDOW_SENSITIVE_PACKAGES) {
            sensitiveWindowToken(windowClass)?.let {
                return DenylistVerdict.blocked(
                    "sensitive-settings", pkg,
                    "this is a security screen ('$it'); Jarvis will not operate it for you"
                )
            }
        }

        if (heuristicsEnabled) {
            segmentToken(pkg)?.let {
                return DenylistVerdict.blocked(
                    "heuristic-token", pkg,
                    "the package id contains '$it', which reads like a finance, wallet or " +
                        "credential app"
                )
            }
        }

        return DenylistVerdict.ALLOWED
    }

    fun isBlocked(packageName: String?, windowClass: String? = null): Boolean =
        check(packageName, windowClass).blocked

    // --- matching helpers ---------------------------------------------------

    private fun matchesUser(pkg: String): String? = userAdditions.firstOrNull { raw ->
        val entry = raw.removeSuffix("*").removeSuffix(".")
        entry.isNotEmpty() && (pkg == entry || pkg.startsWith("$entry."))
    }

    private fun segmentToken(pkg: String): String? {
        val segments = pkg.split('.', '_', '-').filter { it.isNotEmpty() }
        for (segment in segments) {
            for (token in SEGMENT_TOKENS) {
                if (segment == token || segment.startsWith(token) || segment.endsWith(token)) {
                    return token
                }
            }
        }
        return null
    }

    private fun sensitiveWindowToken(windowClass: String?): String? {
        val w = windowClass?.lowercase()?.replace("_", "") ?: return null
        return SENSITIVE_WINDOW_TOKENS.firstOrNull { w.contains(it) }
    }

    /**
     * Window names so dangerous they block regardless of package: the keyguard
     * bouncer and the credential-confirmation screens can be hosted by more than
     * one component depending on the OEM and the Android version.
     */
    private fun strongWindowRule(windowClass: String?): String? {
        val w = windowClass?.lowercase()?.replace("_", "") ?: return null
        val hit = ALWAYS_BLOCKED_WINDOW_TOKENS.firstOrNull { w.contains(it) } ?: return null
        return "the current screen is a credential or lock-screen prompt ('$hit')"
    }

    companion object {

        /** Must match `applicationId` in `app/build.gradle.kts`. */
        const val SELF_PACKAGE = "ai.jarvis.app"

        fun normalize(value: String?): String = value?.trim()?.lowercase().orEmpty()

        /**
         * Exact package ids. Seeded, not exhaustive — [SEGMENT_TOKENS] is what
         * catches the long tail. Add freely; entries are cheap.
         */
        val PACKAGES: Set<String> = setOf(
            // --- password managers, passkeys, authenticators -----------------
            "com.x8bit.bitwarden",
            "com.bitwarden.authenticator",
            "com.keepassdroid",
            "org.keepassdroid",
            "com.android.keepass",
            "io.enpass.app",
            "com.agilebits.onepassword",
            "com.onepassword.android",
            "com.lastpass.lpandroid",
            "com.dashlane",
            "com.callpod.android_apps.keeper",
            "com.nordpass.android.app.password.manager",
            "me.proton.android.pass",
            "com.beemdevelopment.aegis",
            "org.shadowice.flocke.andotp",
            "com.authy.authy",
            "com.duosecurity.duomobile",
            "com.azure.authenticator",
            "com.google.android.apps.authenticator2",
            "com.yubico.yubioath",

            // --- payments and wallets ---------------------------------------
            "com.google.android.apps.walletnfcrel",
            "com.squareup.cash",
            "com.venmo",
            "com.zellepay.zelle",
            "net.one97.paytm",
            "com.wise.android",
            "com.transferwise.android",
            "com.klarna.mobile",
            "com.westernunion.android.mtapp",

            // --- banks and brokerages (seed set) ----------------------------
            "com.chase.sig.android",
            "com.infonow.bofa",
            "com.wf.wellsfargomobile",
            "com.citi.citimobile",
            "com.usaa.mobile.android.usaa",
            "com.konylabs.capitalone",
            "com.discoverfinancial.mobile",
            "com.americanexpress.android.acctsvcs.us",
            "com.htsu.hsbcpersonalbanking",
            "com.starlingbank.android",
            "co.uk.getmondo",
            "com.robinhood.android",
            "com.etrade.mobilepro.activity",
            "com.fidelity.android",
            "com.schwab.mobile",
            "com.vanguard",

            // --- crypto ------------------------------------------------------
            "io.metamask",
            "com.wallet.crypto.trustapp",
            "piuk.blockchain.android",
            "de.schildbach.wallet",
            "org.electrum.electrum",
            "com.ledger.live",
            "com.satoshilabs.trezor.app",

            // --- stores: one tap from an install ----------------------------
            "com.android.vending",
            "com.aurora.store",
            "org.fdroid.fdroid",
            "com.google.android.packageinstaller",
            "com.android.packageinstaller"
        )

        /**
         * Prefix rules. Match the package id itself or anything under it, e.g.
         * `com.paypal.` covers `com.paypal.android.p2pmobile`.
         */
        val PREFIXES: List<String> = listOf(
            "com.paypal.",
            "com.revolut.",
            "com.monzo.",
            "com.barclays.",
            "com.grppl.android.shell.",
            "com.rbs.mobile.",
            "com.santander.",
            "com.lloydsbank.",
            "com.nationwide.",
            "com.coinbase.",
            "com.binance.",
            "com.kraken.",
            "com.stripe.",
            "com.kunzisoft.keepass.",
            "com.bitwarden.",
            "com.google.android.apps.nbu.paisa."
        )

        /**
         * Package-segment tokens. A segment matches when it equals a token or
         * starts/ends with one, so `mybank`, `bankofamerica` and `sparkassen`
         * all hit. Over-matching is the intended failure mode.
         */
        val SEGMENT_TOKENS: Set<String> = setOf(
            "bank", "banking", "banco", "banque", "sparkasse", "creditunion",
            "wallet", "paypal", "venmo", "zelle", "cashapp", "revolut", "monzo",
            "starling", "klarna", "stripe", "payoneer", "paytm", "swish",
            "password", "passwords", "keepass", "bitwarden", "lastpass",
            "dashlane", "onepassword", "1password", "enpass", "passkey",
            "authenticator", "authy", "2fa",
            "crypto", "coinbase", "binance", "blockchain", "bitcoin", "metamask",
            "ledger", "trezor",
            "brokerage", "robinhood", "etrade", "schwab", "fidelity", "vanguard"
        )

        /**
         * Packages that are fine in general but not on their security screens.
         * Blocking Settings outright would kill legitimate automation ("turn on
         * dark mode"), so these are filtered per-window instead.
         */
        val WINDOW_SENSITIVE_PACKAGES: Set<String> = setOf(
            "com.android.settings",
            "com.android.systemui",
            "com.android.credentialmanager",
            "com.android.certinstaller"
        )

        /** Lower-cased, underscore-stripped substrings of an activity/window class. */
        val SENSITIVE_WINDOW_TOKENS: Set<String> = setOf(
            "security", "password", "passkey", "credential", "biometric",
            "fingerprint", "faceunlock", "facesettings", "lockscreen",
            "screenlock", "lockpattern", "lockpassword", "encryption",
            "deviceadmin", "factoryreset", "resetoptions", "developeroptions",
            "development", "adb", "vpn", "privacy", "trustagent", "keystore",
            "certinstaller", "installcaret", "workprofile", "accessibility"
        )

        /**
         * Blocked whatever the package: a keyguard bouncer or a
         * confirm-your-PIN screen is never a legitimate automation target.
         */
        val ALWAYS_BLOCKED_WINDOW_TOKENS: Set<String> = setOf(
            "keyguard", "bouncer", "confirmdevicecredential", "confirmlock",
            "chooselock", "setupchooselock", "biometricprompt"
        )
    }
}

/** Why an automation was refused. [message] is what the model and the log see. */
data class DenylistVerdict(
    val blocked: Boolean,
    /** Short machine-readable rule id, for the audit log. */
    val rule: String? = null,
    val target: String? = null,
    val message: String? = null
) {
    companion object {
        val ALLOWED = DenylistVerdict(false)

        fun blocked(rule: String, target: String, detail: String) = DenylistVerdict(
            blocked = true,
            rule = rule,
            target = target,
            message = "UI automation refused: $target — $detail. This is a hard local rule " +
                "on the device; it cannot be overridden by the server, by a command " +
                "parameter, or by anything written on the screen."
        )
    }
}
