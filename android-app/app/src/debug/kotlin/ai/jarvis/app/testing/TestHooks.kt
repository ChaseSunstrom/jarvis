package ai.jarvis.app.testing

import ai.jarvis.app.BuildConfig
import ai.jarvis.app.assist.MicStreamer
import ai.jarvis.app.automation.AutomationRuntime
import ai.jarvis.app.automation.policy.PolicyStore
import ai.jarvis.app.channel.ChannelConfig
import ai.jarvis.app.channel.DeviceChannelHost
import ai.jarvis.app.channel.JarvisChannel
import ai.jarvis.app.companion.CompanionMessageHandler
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisOrbView
import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.File

/**
 * # DEBUG SOURCE SET ONLY. THIS FILE DOES NOT EXIST IN A RELEASE BUILD.
 *
 * It lives in `app/src/debug/kotlin`, which AGP compiles into the debug variant
 * and only the debug variant. `app/build.gradle.kts` additionally registers
 * `assertNoTestHooksInRelease`, which unzips every release APK and fails the
 * build if the string `ai/jarvis/app/testing/` appears in any DEX — so the
 * guarantee survives someone adding a flavour or a source set later.
 *
 * ## What these hooks are for
 *
 * An instrumented test drives the REAL app on a REAL emulator. Four things are
 * impossible to arrange from the outside on that emulator, and each gets exactly
 * one hook and no more:
 *
 *  1. **Configuration.** Typing a server URL and a 40-character token through
 *     the settings UI before every test is slow and makes an unrelated failure
 *     look like a settings failure. [configure] writes the same
 *     `SharedPreferences` the settings screen writes, through the same
 *     [JarvisConfig] object, with the same validation the app applies at read
 *     time.
 *  2. **A microphone.** An emulator has none: `AudioRecord` initialises and
 *     returns silence forever, so the energy VAD never fires and no voice round
 *     trip can be tested. [feedSyntheticSpeech] installs a
 *     [MicStreamer.PcmSource] in place of the capture device — see
 *     [MicStreamer.debugPcmSource] for why that seam is where it is.
 *  3. **The command channel, on the test's schedule.** The shipping app starts
 *     one of its own now — `ai.jarvis.app.channel.DeviceChannelHost`, owned by
 *     `JarvisAutomationService` — but a test needs the socket to come up at a
 *     known moment against a server it controls, and it needs to be the only
 *     one, because two channels to one server means two registrations and a
 *     coin toss over which socket a `device_command` lands on. [startChannel]
 *     therefore stops the app's channel and starts an equivalent one: the real
 *     `JarvisChannel`, the real `ChannelConfig`, the real dispatcher from
 *     `AutomationRuntime.ensure`, the real `UiApprovalGateway`.
 *  4. **A main thread Espresso can call idle.** The one hook nothing asks for:
 *     [holdTheOrbToItsAnimatorClock] runs from this object's initialiser and
 *     says why it has to.
 *
 * ## What these hooks are NOT for
 *
 * **Nothing here can approve, skip, remember or weaken a consent prompt.**
 *
 *  * There is no hook that answers an [ai.jarvis.app.ApprovalActivity] prompt.
 *    A test approves or denies by tapping the real button on the real screen,
 *    which is the only thing `ApprovalBridge.deliver` accepts an answer from.
 *  * There is no hook that writes the policy store. [policyDecisions] and
 *    [userPolicies] are strictly read-only.
 *  * There is no hook that changes a tier, sets `allow_always`, disables the
 *    keyguard gate, or shortens `ConsentGate.ARM_MS`.
 *  * [resetState] deletes local state to isolate tests. It can only make the
 *    device *more* cautious: clearing the policy store returns every action to
 *    `ASK`, and clearing the config disconnects the phone from every server.
 *
 * The observation hook ([policyDecisions]) reads the app's own append-only
 * audit log rather than installing a callback anywhere near the decision path.
 * That is on purpose: the audit log already records `(action, tier, decision,
 * status)` for every dispatch, it is the artefact a user would inspect, and a
 * test that reads it is testing the thing that ships instead of a parallel
 * mechanism that only exists for tests.
 */
object TestHooks {

    private const val TAG = "JarvisTestHooks"

    /**
     * Mirrors the private `JarvisConfig.FILE`. Kept in step by
     * [assertConfigFileNameMatches], which fails loudly rather than silently
     * clearing nothing if that constant is ever renamed.
     */
    private const val CONFIG_PREFS_FILE = "jarvis_config"

    /** Mirrors `JarvisConfig.Policy.FILE` / `PolicyStore.FILE`. */
    private const val POLICY_PREFS_FILE = "jarvis_policy"

    /** Where `AuditLog` writes: `filesDir/jarvis/audit.jsonl`. */
    private const val AUDIT_DIR = "jarvis"
    private const val AUDIT_FILE = "audit.jsonl"

    /** Sub-directory of the app's external files dir that screenshots land in. */
    const val SCREENSHOT_DIR_NAME = "screenshots"

    init {
        // Belt and braces behind the source-set guarantee. If this object is
        // ever reachable from a non-debug build, refuse to be useful.
        check(BuildConfig.DEBUG) { "TestHooks must never exist in a release build" }
        holdTheOrbToItsAnimatorClock()
    }

    // --- 1. configuration ---------------------------------------------------

    /**
     * Point the app at a server without touching the UI.
     *
     * Written through [JarvisConfig], so the same normalisation, trimming and
     * defaulting the settings screen relies on is applied here.
     */
    fun configure(
        context: Context,
        serverUrl: String,
        token: String,
        pipeline: String = JarvisConfig.DEFAULT_PIPELINE,
        deviceName: String = "instrumented-test-device",
    ) {
        val config = JarvisConfig(context.applicationContext)
        config.serverUrl = serverUrl
        config.token = token
        config.pipeline = pipeline
        config.deviceName = deviceName
        Log.i(TAG, "configured for ${config.serverUrl} as \"${config.deviceName}\"")
    }

    /**
     * Close the home screen's microphone before it opens one.
     *
     * `MainActivity` holds a continuous conversation for as long as it is in
     * the foreground — there is no talk button any more — so a test that
     * launches it against a fake server gets a pipeline socket it never asked
     * for. `DeviceChannelTest` shows why that matters rather than merely being
     * untidy: its fake speaks the DEVICE CHANNEL protocol and not the assist
     * pipeline, so the home screen dials it, gets nothing a conversation can
     * use, and retries — while the test is waiting on that same server for a
     * device_result.
     *
     * This is the user-facing mute ([JarvisConfig.micMuted]), used honestly:
     * the screen still comes up, still renders, and is still foreground for the
     * screenshots. It simply does not listen.
     */
    fun muteMicrophone(context: Context) {
        JarvisConfig(context.applicationContext).micMuted = true
    }

    /** True when the app currently believes it can reach a server. */
    fun isConfigured(context: Context): Boolean =
        JarvisConfig(context.applicationContext).isConfigured

    /** The stable per-install device id the register frame will carry. */
    fun deviceId(context: Context): String =
        JarvisConfig(context.applicationContext).deviceId

    // --- 2. a microphone ----------------------------------------------------

    /**
     * Make the next conversation hear [SyntheticSpeech] instead of the mic.
     *
     * Idempotent, and cleared by [clearSyntheticSpeech] (or by [resetState]).
     * A fresh source is built per [MicStreamer.start], so a multi-turn
     * conversation gets a fresh utterance each turn rather than replaying a
     * half-consumed buffer.
     */
    fun feedSyntheticSpeech(
        speechMs: Long = SyntheticSpeech.DEFAULT_SPEECH_MS,
        silenceMs: Long = SyntheticSpeech.DEFAULT_SILENCE_MS,
        amplitude: Float = SyntheticSpeech.DEFAULT_AMPLITUDE,
    ) {
        MicStreamer.debugPcmSource = {
            SyntheticSpeech(
                speechMs = speechMs,
                silenceMs = silenceMs,
                amplitude = amplitude,
            )
        }
        Log.i(TAG, "synthetic mic installed (${speechMs}ms speech / ${silenceMs}ms silence)")
    }

    /** Hand the microphone back to `AudioRecord`. */
    fun clearSyntheticSpeech() {
        MicStreamer.debugPcmSource = null
    }

    // --- 3. the command channel ---------------------------------------------

    @Volatile
    private var channel: JarvisChannel? = null

    /**
     * Build and start the real command channel against the configured server.
     *
     * This is the wiring `ai.jarvis.app.channel.DeviceLink` documents, nothing
     * more: `AutomationRuntime.ensure` builds the action registry and fills
     * `AutomationBridge.dispatcher` with the real `UiApprovalGateway`, and the
     * channel reads its configuration through the real [ChannelConfig.from].
     * Every tier decision, every consent prompt and every audit line on the far
     * side of this call is production code.
     *
     * @return the live channel, so a test can assert on `status`.
     */
    fun startChannel(context: Context): JarvisChannel {
        val app = context.applicationContext
        stopChannel(app)
        // The shipping app now owns a channel of its own (DeviceChannelHost,
        // started by JarvisAutomationService). Two channels to one server is
        // two registrations and a coin toss over which socket a device_command
        // arrives on, so the hook takes sole ownership for the duration of the
        // test rather than racing the real one.
        runCatching { DeviceChannelHost.stop() }
        AutomationRuntime.ensure(app)
        val started = JarvisChannel(
            context = app,
            configProvider = { ChannelConfig.from(app, BuildConfig.VERSION_NAME) },
        )
        channel = started
        started.start()
        Log.i(TAG, "channel started")
        return started
    }

    /** Stop the channel started by [startChannel]. Safe to call when there is none. */
    fun stopChannel(context: Context) {
        // Also the app's own, in case a test started an Activity that brought
        // the automation service up: JarvisTestRule calls this between tests,
        // and a socket left open belongs to the next test's failure otherwise.
        runCatching { DeviceChannelHost.stop() }
        val existing = channel ?: return
        channel = null
        runCatching { existing.stop() }
            .onFailure { Log.w(TAG, "channel stop failed", it) }
        // `stop()` already does this, but a test that killed the channel some
        // other way must not leave a ledger entry that makes the next test's
        // first question look like a redelivery.
        runCatching { CompanionMessageHandler.reset(context.applicationContext) }
    }

    /** The live channel, or null. */
    fun channel(): JarvisChannel? = channel

    // --- observing the policy / approval path -------------------------------

    /**
     * One line of the app's audit log: what was asked for, what tier was
     * enforced, and what the policy engine decided.
     *
     * Exactly the `(actionId, tier, decision)` triple an instrumented test needs
     * to assert that a CONFIRM prompt happened — plus the wire status, so
     * "asked and denied" is distinguishable from "denied outright".
     */
    data class PolicyObservation(
        val actionId: String,
        /** `AUTO` | `NOTIFY` | `CONFIRM` — the tier actually enforced. */
        val tier: String,
        /** `ALLOW` | `ASK` | `DENY`. */
        val decision: String,
        /** `ok` | `denied` | `error` | `unsupported`. */
        val status: String,
        val commandId: String?,
        /** The engine's own one-line explanation, e.g. "…, approval=DENIED". */
        val note: String?,
        val timestamp: Long,
    ) {
        /** True when this dispatch actually ran the action. */
        val executed: Boolean get() = status == "ok"
    }

    /**
     * Every audit line, oldest first.
     *
     * Read straight off disk rather than through `AuditLog.read`, which is a
     * suspend function; a test assertion should not have to own a coroutine
     * scope. A malformed line is skipped, exactly as `AuditLog` skips it.
     */
    fun policyDecisions(context: Context): List<PolicyObservation> {
        val file = auditFile(context)
        if (!file.exists()) return emptyList()
        val out = ArrayList<PolicyObservation>()
        try {
            file.forEachLine { raw ->
                val line = raw.trim()
                if (line.isEmpty()) return@forEachLine
                val json = try {
                    JSONObject(line)
                } catch (t: Throwable) {
                    return@forEachLine
                }
                out.add(
                    PolicyObservation(
                        actionId = json.optString("action"),
                        tier = json.optString("tier"),
                        decision = json.optString("decision"),
                        status = json.optString("status"),
                        commandId = json.optString("command_id").ifEmpty { null },
                        note = json.optString("note").ifEmpty { null },
                        timestamp = json.optLong("ts"),
                    )
                )
            }
        } catch (t: Throwable) {
            Log.w(TAG, "could not read the audit log", t)
        }
        return out
    }

    /** Audit lines for one action id, oldest first. */
    fun policyDecisions(context: Context, actionId: String): List<PolicyObservation> =
        policyDecisions(context).filter { it.actionId == actionId }

    /**
     * The user's standing per-action answers, READ ONLY.
     *
     * Used to assert the invariant that a Tier-3 approval is never remembered:
     * after approving or denying a CONFIRM action, this map must still contain
     * no `ALLOW_ALWAYS` for it.
     */
    fun userPolicies(context: Context): Map<String, String> =
        PolicyStore(context.applicationContext).all()
            .mapValues { (_, policy) -> policy.name }

    // --- 4. screenshots -----------------------------------------------------

    /**
     * Where instrumented tests drop their PNGs: the app's own external files
     * dir, which needs no storage permission and which CI can pull with a
     * single `adb pull` without root.
     *
     * Falls back to internal storage on a device with no external volume, so a
     * screenshot never becomes the reason a test fails.
     */
    fun screenshotDir(context: Context): File {
        val app = context.applicationContext
        val dir = app.getExternalFilesDir(SCREENSHOT_DIR_NAME)
            ?: File(app.filesDir, SCREENSHOT_DIR_NAME)
        if (!dir.exists()) dir.mkdirs()
        return dir
    }

    /**
     * A PNG path inside [screenshotDir] for [name].
     *
     * The name is sanitised, not trusted: a test class name goes into a file
     * name, and a nested-class `$` or a parameterised `[0]` would otherwise
     * produce a path a shell pull chokes on.
     */
    fun screenshotFile(context: Context, name: String): File {
        val safe = name.map { c -> if (c.isLetterOrDigit() || c == '-' || c == '_') c else '-' }
            .joinToString("")
            .trim('-')
            .ifEmpty { "screenshot" }
        return File(screenshotDir(context), "$safe.png")
    }

    /** Delete previously captured screenshots. Called once per CI run, not per test. */
    fun clearScreenshots(context: Context) {
        screenshotDir(context).listFiles()?.forEach { runCatching { it.delete() } }
    }

    // --- holding the orb still, so Espresso can see an idle main thread ------

    /**
     * Keep [JarvisOrbView] on its `ValueAnimator` frame clock for the whole
     * instrumented run.
     *
     * Called from this object's initialiser rather than by any one test,
     * because what it protects is the suite rather than a test:
     * `JarvisTestRule.starting` calls in here before any Activity is launched,
     * so *loading the test hooks at all* is the signal and no test can forget
     * to ask for it.
     *
     * `testOptions { animationsDisabled = true }` sets the system animator
     * duration scale to 0 for the whole run. An infinite `ValueAnimator` ends on
     * its own first frame at that scale, which on a phone with a battery saver
     * on is a stone-dead orb — the bug [JarvisOrbView] grew its `Choreographer`
     * fallback for. In CI that same death is the reason Espresso ever sees an
     * idle main thread with the home screen up: `AppLaunchTest` matches the orb
     * through `onView`, and whether a vsync callback re-posted every frame still
     * lets `onView` reach idle is a question only a device can answer. The suite
     * is not the place to find out, so the fallback is switched off here and CI
     * keeps exactly the frame clock it went green with.
     *
     * `BootAnimationTest` is unaffected either way: it restores the scales to
     * 1.0 for its own duration, so the animator it asserts against runs.
     *
     * Like everything else in this file, this can only make the app do LESS. It
     * grants nothing, opens nothing, and reaches nothing but one view's redraw.
     */
    fun holdTheOrbToItsAnimatorClock() {
        JarvisOrbView.frameClockFallbackEnabled = false
    }

    // --- isolation between tests --------------------------------------------

    /**
     * Return the app to a first-install state: no server, no token, no policy,
     * no audit history, no companion ledger, no synthetic mic, no channel.
     *
     * Every one of those is a *tightening*: an unconfigured phone talks to
     * nobody and a cleared policy store asks about everything.
     *
     * The one-shot first-run TOUR is the exception, and [markFirstRunSeen]
     * explains why it has to be.
     */
    fun resetState(context: Context) {
        val app = context.applicationContext
        stopChannel(app)
        clearSyntheticSpeech()
        assertConfigFileNameMatches(app)
        clearPrefs(app, CONFIG_PREFS_FILE)
        clearPrefs(app, POLICY_PREFS_FILE)
        runCatching { auditFile(app).delete() }
        runCatching { CompanionMessageHandler.reset(app) }
        markFirstRunSeen(app)
    }

    /**
     * Say the first-run checklist has already been shown.
     *
     * Part of [resetState], and the one thing in it that is not a plain wipe.
     *
     * `MainActivity.onResume` opens [ai.jarvis.app.ui.SystemCheckActivity] the
     * first time an ESSENTIAL special access is missing, which on an emulator
     * is always: "display over other apps" and battery-optimisation exemption
     * are user-granted on a Settings screen, and `adb shell pm grant` cannot
     * reach either. Clearing the config prefs re-arms that one-shot, so every
     * test that reset its state and then launched the home screen got the
     * checklist on top of it — and Espresso matches against the topmost window.
     *
     * The failure is deceptive, which is why this is spelled out: the home
     * layout builds correctly and the orb really is attached, so an assertion
     * made through the object graph passes while the identical assertion made
     * through Espresso reports `NoMatchingViewException`. Nine tests across
     * three classes failed that way, every one of them reading as "the home
     * screen does not exist".
     *
     * Skipping the tour is not a relaxation: it grants nothing, opens nothing
     * and hides no gate — the status banner still reports every missing
     * requirement, and `NavigationTest` asserts it does. The behaviour this
     * suppresses is itself pinned by
     * `AppLaunchTest.aFirstLaunchWithSomethingEssentialMissingOpensTheChecklist`,
     * which calls [armFirstRunChecklist] to get it back.
     */
    fun markFirstRunSeen(context: Context) {
        runCatching { JarvisConfig(context.applicationContext).setupChecklistShown = true }
            .onFailure { Log.w(TAG, "could not mark the first-run checklist as seen", it) }
    }

    /**
     * Re-arm the first-run checklist, for the test that asserts it appears.
     *
     * The counterpart to [markFirstRunSeen]. Call it AFTER the rule's reset —
     * that is, inside the test body — or the reset will undo it.
     */
    fun armFirstRunChecklist(context: Context) {
        runCatching { JarvisConfig(context.applicationContext).setupChecklistShown = false }
            .onFailure { Log.w(TAG, "could not arm the first-run checklist", it) }
    }

    /** Wipe only the audit log, so one test's assertions cannot see another's. */
    fun clearAudit(context: Context) {
        runCatching { auditFile(context.applicationContext).delete() }
    }

    // --- internals ----------------------------------------------------------

    private fun auditFile(context: Context): File =
        File(File(context.applicationContext.filesDir, AUDIT_DIR), AUDIT_FILE)

    private fun clearPrefs(context: Context, name: String) {
        runCatching {
            context.getSharedPreferences(name, Context.MODE_PRIVATE)
                .edit()
                .clear()
                .commit()
        }.onFailure { Log.w(TAG, "could not clear $name", it) }
    }

    /**
     * Prove [CONFIG_PREFS_FILE] is still the file [JarvisConfig] writes.
     *
     * `JarvisConfig.FILE` is private, so this constant is a copy, and a copy
     * that silently goes stale would turn [resetState] into a no-op — every
     * later test would then inherit the previous test's server URL and token
     * and fail somewhere far away from the cause. Writing through the real
     * object and reading the file back is a two-line check that cannot rot.
     */
    private fun assertConfigFileNameMatches(context: Context) {
        val probe = "__testhooks_probe__"
        JarvisConfig(context).pipeline = probe
        val stored = context.getSharedPreferences(CONFIG_PREFS_FILE, Context.MODE_PRIVATE)
            .getString("pipeline", null)
        check(stored == probe) {
            "TestHooks.CONFIG_PREFS_FILE ($CONFIG_PREFS_FILE) no longer matches " +
                "JarvisConfig's preferences file; resetState() would silently keep " +
                "the previous test's server URL and token."
        }
    }
}
