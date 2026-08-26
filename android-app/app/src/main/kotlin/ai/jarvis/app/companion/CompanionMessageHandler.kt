package ai.jarvis.app.companion

import ai.jarvis.app.assist.ConversationRegistry
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.util.Log
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/**
 * The device end of cross-device conversation: one inbound `jarvis_message`
 * goes in, exactly one `jarvis_message_result` comes out.
 *
 * ```
 *   JarvisChannel.onText()
 *        │  type == "jarvis_message"
 *        ▼
 *   CompanionMessageHandler.handle(context, frame)
 *        │
 *        ├─ mode "ask"    ──► CompanionAskActivity  (+ a high-priority
 *        │                    notification, so a refused background start
 *        │                    still leaves the user a way in)
 *        ├─ mode "speak"  ──► the orb, when a surface is on screen;
 *        │                    otherwise a notification that speaks when opened
 *        ├─ mode "notify" ──► a Jarvis-style notification
 *        └─ anything else ──► "undeliverable", so the server escalates
 *        │
 *        ▼
 *   sender.send({"type":"jarvis_message_result", ...})
 * ```
 *
 * ## Wiring
 *
 * ```kotlin
 * // wherever the socket is owned:
 * CompanionMessageHandler.sender = CompanionMessageHandler.Sender { frame ->
 *     channel.sendFrame(frame)          // raw frame, not a device_event
 * }
 * // and in the channel's inbound `when (msg.optString("type"))`:
 * CompanionProtocol.TYPE_MESSAGE -> CompanionMessageHandler.handle(appContext, msg)
 * ```
 *
 * `CompanionAskActivity` must be declared in the manifest (exported=false,
 * showWhenLocked, turnScreenOn) for the ask path to raise its own screen; with
 * no declaration the start fails, the notification carries the question, and
 * nothing crashes.
 *
 * ## What this class refuses to do
 *
 * * **Answer twice.** [CompanionLedger] is consulted on every path. A
 *   redelivered id replays the stored reply verbatim and prompts nobody; an
 *   id already on screen is ignored; a countdown that fires after a tap is
 *   refused. The server escalates on anything that is not `answered`, so a
 *   second, different reply would push a question the user already dealt with
 *   onto another device.
 * * **Go quiet on failure.** No notification permission, no activity, an
 *   unknown mode, an id nobody remembers: every one of them is a *reported*
 *   `undeliverable`, because a message this device silently drops is a message
 *   that reaches the user nowhere.
 * * **Run anything.** There is no import of the action registry, the
 *   dispatcher or the policy store in this package, and no field on
 *   [CompanionProtocol.Message] that could name an action. A proactive message
 *   is information and questions only; acting on an answer goes back through
 *   `device_command` and the full Tier-1/2/3 treatment, so "yes" is data and
 *   never an authorisation token.
 */
object CompanionMessageHandler {

    /** How the result frame gets back onto the socket. */
    fun interface Sender {
        fun send(frame: JSONObject): Boolean
    }

    private const val TAG = "JarvisCompanion"

    /**
     * Slack on top of a question's own countdown before this class answers
     * `timeout` itself. The activity's timer should always win; this is the net
     * under a question whose screen never appeared at all.
     */
    const val WATCHDOG_GRACE_MS = 15_000L

    @Volatile
    var sender: Sender? = null

    /**
     * The on-screen surface that can speak. Set in `onResume`, cleared in
     * `onPause` — see [CompanionSpeechHost].
     */
    @Volatile
    var speechHost: CompanionSpeechHost? = null

    /** The one "have we answered this?" ledger. */
    val ledger = CompanionLedger()

    private val main = Handler(Looper.getMainLooper())
    private val watchdogs = ConcurrentHashMap<String, Runnable>()

    /**
     * Clear [speechHost], but only if [host] is still the registered one — two
     * surfaces handing over must not leave the slot pointing at the one that
     * went away first. A null argument clears unconditionally.
     */
    fun clearSpeechHost(host: CompanionSpeechHost?) {
        if (host == null || speechHost === host) speechHost = null
    }

    // --- inbound ------------------------------------------------------------

    /**
     * Handle one inbound frame. Returns false when it was not a
     * `jarvis_message` this device can act on, so a caller can keep its own
     * dispatch table honest.
     *
     * Called on the socket's reader thread. The admission decision is taken
     * there — it is a synchronized map lookup, and taking it inline is what
     * makes a redelivery arriving hard behind the first one see `InFlight`
     * rather than raising a second copy of the question. Everything after it is
     * handed to the main thread; see [present].
     */
    fun handle(context: Context, frame: JSONObject): Boolean {
        val message = CompanionProtocol.parse(frame)
        if (message == null) {
            // No message_id means no way to answer and no way for the server to
            // match a reply. Dropping is the only option that does not invent
            // an id it never sent.
            Log.w(TAG, "ignoring a jarvis_message with no usable message_id")
            return false
        }
        val app = context.applicationContext

        when (val admission = ledger.admit(message.messageId)) {
            is CompanionLedger.Admission.Settled -> {
                // A redelivery. The socket may have died between our answer and
                // the server reading it, so replay the stored reply verbatim and
                // prompt nobody.
                Log.i(TAG, "replaying the stored ${admission.status} for ${message.messageId}")
                transmit(admission.reply)
                return true
            }
            CompanionLedger.Admission.InFlight -> {
                Log.d(TAG, "${message.messageId} is already on screen; ignoring the redelivery")
                return true
            }
            CompanionLedger.Admission.Fresh -> Unit
        }

        onMain { present(app, message) }
        return true
    }

    /**
     * Put the message in front of the user. MAIN THREAD ONLY.
     *
     * Every branch either touches a View — [CompanionSpeechHost] drives the orb,
     * and `JarvisOrbView.setMode` starts a `ValueAnimator`, which needs a
     * Looper and throws on a bare thread — or makes a binder round trip
     * (`NotificationManager.notify`, `startActivity`). The caller is the
     * WebSocket reader thread, which has neither a Looper nor any business
     * waiting on the system server: the same reason `sendRegister` is pushed
     * off it in [ai.jarvis.app.channel.JarvisChannel].
     */
    private fun present(app: Context, message: CompanionProtocol.Message) {
        // THE HANDOFF, on this end.
        //
        // `companion.handoff` is documented in `docs/cross-device.md` as moving
        // an in-flight conversation to another device, and grepping this package
        // for "handoff" used to return nothing at all. It turns out there is no
        // `handoff` frame to implement: the server's service (see
        // `jarvis/integrations/companion/__init__.py`) is an ordinary
        // `manager.send(kind="say", conversation_id=…)` aimed at a chosen
        // device. The move IS the conversation_id on a normal message.
        //
        // So this is the whole of it, and it was the missing line: adopt the
        // thread the message arrived on, so the next thing said to this phone
        // continues it. `CompanionProtocol` parsed the field, the handler put it
        // in an intent extra, and nothing anywhere read it — the documented
        // continuity reached the device and was dropped.
        //
        // Adopted for every mode, not just `ask`. A handoff is a `say`, and a
        // `notify` that names a conversation is the server telling this device
        // which thread the user is now in.
        ConversationRegistry.remember(app, message.conversationId)
        when (message.mode) {
            CompanionProtocol.MODE_ASK -> ask(app, message)
            CompanionProtocol.MODE_SPEAK -> speak(app, message)
            CompanionProtocol.MODE_NOTIFY -> notify(app, message)
            else -> {
                Log.w(TAG, "unknown companion mode '${message.mode}'; reporting undeliverable")
                settle(app, message.messageId, CompanionProtocol.STATUS_UNDELIVERABLE, null)
            }
        }
    }

    private inline fun onMain(crossinline block: () -> Unit) {
        if (Looper.myLooper() === Looper.getMainLooper()) block() else main.post { block() }
    }

    /**
     * The answer, from [CompanionAskActivity]. Ignored for an id that has
     * already been answered — a stale screen must not overwrite a real choice.
     */
    fun onAnswer(context: Context, messageId: String, status: String, answer: String? = null) {
        settle(context, messageId, status, answer)
    }

    /**
     * Report `undeliverable` for an id this device cannot honour.
     *
     * The case that matters: [CompanionAskActivity] is restored after the
     * process was killed, so the ledger no longer knows the id and there is
     * nobody left waiting for the answer. Saying so lets the server escalate
     * immediately instead of waiting out the whole timeout; staying quiet would
     * cost the user the message.
     */
    fun reportUndeliverable(context: Context, messageId: String) {
        settle(context, messageId, CompanionProtocol.STATUS_UNDELIVERABLE, null)
    }

    // --- the three modes ----------------------------------------------------

    private fun ask(app: Context, message: CompanionProtocol.Message) {
        // Ask on the surface the user is already looking at, if there is one.
        //
        // The alternative below starts a full-screen activity with NEW_TASK,
        // which takes down whatever conversation was on screen — and takes
        // itself down when answered, leaving nothing. Reported as: the
        // wake-word orb closes when Jarvis asks something, and closes again
        // when you answer.
        //
        // Only for a plain spoken question. A question with options is a list
        // to choose from and a voice answer cannot be matched to one of them
        // without the model in the loop, which is exactly what this path exists
        // to keep out; those still go to the screen that can draw buttons.
        val host = speechHost
        if (message.spoken && host != null && host.isForeground) {
            // THE SINGLE VOICE (M66). The conversation on screen is the one
            // speaking the reply that carries this question, and the next
            // thing said to it is the answer — the server resolves a spoken
            // answer from the turn itself. Asking again here, aloud, is the
            // double the operator reported: "it says both the response and
            // the question". `dismissed` is what "not dealt with on this
            // device" means everywhere in this protocol; the server may then
            // offer it on another device as a card, which is fine.
            settle(app, message.messageId, CompanionProtocol.STATUS_DISMISSED, null)
            return
        }
        if (host != null && message.options.isEmpty() && host.isForeground) {
            val taken = try {
                host.ask(message.text) { answer ->
                    settle(
                        app,
                        message.messageId,
                        if (answer.isNullOrBlank()) CompanionProtocol.STATUS_DISMISSED
                        else CompanionProtocol.STATUS_ANSWERED,
                        answer,
                    )
                }
            } catch (t: Throwable) {
                // A surface that blows up must not swallow the question.
                Log.w(TAG, "the on-screen surface could not take the question", t)
                false
            }
            if (taken) {
                armWatchdog(app, message)
                return
            }
        }

        val intent = askIntent(app, message)

        // Notification first. If the direct start is refused by background
        // activity-start restrictions this is the user's only route to the
        // question, and posting it after a failed start would be too late.
        var reachable = CompanionNotifications.post(app, message, intent)
        try {
            app.startActivity(intent)
            reachable = true
        } catch (t: Throwable) {
            Log.w(TAG, "direct start of the question screen was refused", t)
        }

        if (!reachable) {
            settle(app, message.messageId, CompanionProtocol.STATUS_UNDELIVERABLE, null)
            return
        }
        armWatchdog(app, message)
    }

    private fun speak(app: Context, message: CompanionProtocol.Message) {
        if (message.text.isBlank()) {
            settle(app, message.messageId, CompanionProtocol.STATUS_UNDELIVERABLE, null)
            return
        }
        val host = speechHost
        if (host != null && host.isForeground) {
            val started = try {
                host.speak(message.text) { spoken ->
                    if (spoken) {
                        settle(app, message.messageId, CompanionProtocol.STATUS_ANSWERED, "")
                    } else {
                        // Synthesis or playback failed. A notification is a
                        // downgrade, not a drop — the same rule the server
                        // applies when it routes a speech message to a device
                        // with no audio.
                        notifyOrFail(app, message)
                    }
                }
            } catch (t: Throwable) {
                Log.w(TAG, "the speech host threw", t)
                false
            }
            if (started) {
                // The host owns the outcome now, but not forever.
                armWatchdog(app, message)
                return
            }
        }
        // Nothing on screen to speak through: raise it as a notification that
        // speaks when it is opened.
        notifyOrFail(app, message)
    }

    private fun notify(app: Context, message: CompanionProtocol.Message) {
        notifyOrFail(app, message)
    }

    private fun notifyOrFail(app: Context, message: CompanionProtocol.Message) {
        val shown = CompanionNotifications.post(app, message, askIntent(app, message))
        if (message.wantsAnswer) {
            // Belt and braces behind the rule [CompanionProtocol.parse]
            // enforces. A posted notification is DELIVERY, and this protocol
            // spells delivery `answered` because there is no fifth status — but
            // a question is answered by a person choosing something, never by
            // this phone confirming it drew a notification. If a question ever
            // reaches here, leave it unsettled: the user can still tap through
            // and answer it, and the watchdog reports `timeout` if they do not,
            // which is what makes the server escalate.
            if (!shown) {
                settle(app, message.messageId, CompanionProtocol.STATUS_UNDELIVERABLE, null)
            } else {
                armWatchdog(app, message)
            }
            return
        }
        settle(
            app,
            message.messageId,
            if (shown) CompanionProtocol.STATUS_ANSWERED
            else CompanionProtocol.STATUS_UNDELIVERABLE,
            "",
        )
    }

    private fun askIntent(app: Context, message: CompanionProtocol.Message): Intent =
        Intent(app, CompanionAskActivity::class.java)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS or
                    Intent.FLAG_ACTIVITY_NO_USER_ACTION
            )
            .putExtra(EXTRA_MESSAGE_ID, message.messageId)
            .putExtra(EXTRA_MODE, message.mode)
            .putExtra(EXTRA_TEXT, message.text)
            .putExtra(EXTRA_OPTIONS, message.options.toTypedArray())
            .putExtra(EXTRA_IMPORTANCE, message.importance)
            .putExtra(EXTRA_CONVERSATION_ID, message.conversationId)
            .putExtra(EXTRA_TIMEOUT_MS, message.timeoutMs)
            .putExtra(EXTRA_SPOKEN, message.spoken)

    // --- answering ----------------------------------------------------------

    private fun settle(context: Context, messageId: String, status: String, answer: String?) {
        val id = messageId.trim()
        if (id.isEmpty()) return
        val frame = CompanionProtocol.result(id, status, answer)
        val text = frame.toString()
        if (!ledger.settle(id, frame.optString("status"), text)) {
            Log.d(TAG, "$id was already answered; ignoring a second $status")
            return
        }
        disarmWatchdog(id)
        CompanionNotifications.cancel(context, id)
        transmit(text)
    }

    private fun transmit(frameText: String) {
        val target = sender
        if (target == null) {
            Log.w(TAG, "no sender wired; the companion result cannot be delivered")
            return
        }
        val frame = try {
            JSONObject(frameText)
        } catch (t: Throwable) {
            Log.w(TAG, "could not rebuild a stored result frame", t)
            return
        }
        val sent = try {
            target.send(frame)
        } catch (t: Throwable) {
            Log.w(TAG, "sending the companion result failed", t)
            false
        }
        if (!sent) {
            // Kept in the ledger regardless: the server redelivers what it did
            // not hear an answer to, and the redelivery replays this exact
            // frame rather than asking the user a second time.
            Log.w(TAG, "the companion result was not delivered; it will be replayed")
        }
    }

    // --- the watchdog -------------------------------------------------------

    /**
     * Answer `timeout` if nothing else has, [WATCHDOG_GRACE_MS] after the
     * question's own deadline.
     *
     * Without it, a question whose screen never appeared — a notification the
     * user never taps, a speech host that never calls back — would leave the
     * automation on the server blocked until *its* timeout, and would never
     * escalate to the device the user is actually at.
     */
    private fun armWatchdog(app: Context, message: CompanionProtocol.Message) {
        disarmWatchdog(message.messageId)
        val id = message.messageId
        val runnable = Runnable {
            watchdogs.remove(id)
            if (ledger.statusOf(id) == null) {
                Log.i(TAG, "nobody answered $id in time; reporting timeout")
                settle(app, id, CompanionProtocol.STATUS_TIMEOUT, null)
            }
        }
        watchdogs[id] = runnable
        main.postDelayed(runnable, message.timeoutMs + WATCHDOG_GRACE_MS)
    }

    private fun disarmWatchdog(messageId: String) {
        watchdogs.remove(messageId)?.let { main.removeCallbacks(it) }
    }

    /**
     * Forget everything. Called when the socket goes away for good: the server
     * got no answer for anything still in flight, so a redelivery after the
     * next connection is free to ask again.
     */
    fun reset(context: Context? = null) {
        for (runnable in watchdogs.values) main.removeCallbacks(runnable)
        watchdogs.clear()
        ledger.clear()
        speechHost = null
        // A question whose ledger entry has just gone can no longer be
        // answered; leaving its notification up offers a control that does
        // nothing. Also the only thing that bounds `posted` across a long
        // uptime, since it is otherwise trimmed only by an answer.
        context?.let { CompanionNotifications.cancelAll(it) }
    }

    // --- intent extras ------------------------------------------------------

    const val EXTRA_MESSAGE_ID = "ai.jarvis.app.companion.MESSAGE_ID"
    const val EXTRA_MODE = "ai.jarvis.app.companion.MODE"
    const val EXTRA_TEXT = "ai.jarvis.app.companion.TEXT"
    const val EXTRA_OPTIONS = "ai.jarvis.app.companion.OPTIONS"
    const val EXTRA_IMPORTANCE = "ai.jarvis.app.companion.IMPORTANCE"
    const val EXTRA_CONVERSATION_ID = "ai.jarvis.app.companion.CONVERSATION_ID"
    const val EXTRA_TIMEOUT_MS = "ai.jarvis.app.companion.TIMEOUT_MS"
    const val EXTRA_SPOKEN = "ai.jarvis.app.companion.SPOKEN"
}
