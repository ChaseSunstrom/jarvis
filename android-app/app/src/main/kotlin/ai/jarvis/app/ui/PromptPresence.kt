package ai.jarvis.app.ui

import android.util.Log
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger

/**
 * "Jarvis is asking the user something right now."
 *
 * ## Why this exists
 *
 * Reported, twice, and the second time after a fix that could not have helped:
 *
 *     "the prompt and such with the siri orb overlay, is still closing the
 *      overlay when I try to approve permissions ... and it still forces me to
 *      click on the tool call to approve"
 *
 * There are **two** "Hey Jarvis" surfaces, and only one of them is an Activity:
 *
 *  * [ai.jarvis.app.JarvisAssistActivity] — the assist-gesture card. An
 *    Activity, so a full-screen prompt appearing over it calls `onStop`, and it
 *    can hold the conversation from there.
 *  * `assist.AssistOverlay` — a `TYPE_APPLICATION_OVERLAY` **window**, put up by
 *    `WakeWordService` whenever it can. This is what the wake word shows, which
 *    is to say it is the one people actually see.
 *
 * An overlay window has no activity lifecycle at all. Nothing calls `onStop` on
 * it, nothing calls `onStart`, and a prompt appearing over it is — as far as it
 * is concerned — nothing happening. So the fix that taught the ACTIVITY to hold
 * its conversation through a prompt did not apply to the surface being reported,
 * and could not have.
 *
 * What happened on the overlay instead was a **timer**. `JarvisConversation`
 * runs an 8-second inactivity timer, and a user reading a consent prompt is a
 * user saying nothing: eight seconds in, `Ui.onIdle` fired, `WakeWordService`
 * ran `endOverlayConversation`, and the orb vanished out from under the prompt.
 * `holdForQuestion` is precisely the call that prevents that — its own KDoc says
 * "`running` stays true throughout, deliberately ... so an inactivity timer or
 * an `onIdle` cannot pull the surface out from under the question" — and it had
 * exactly two callers, both for QUESTIONS. An approval held nothing.
 *
 * ## The other half
 *
 * A `TYPE_APPLICATION_OVERLAY` window is drawn **above every Activity**. The orb
 * is a 340dp card anchored 72dp off the bottom; `ApprovalActivity` puts DENY and
 * APPROVE at the end of its column. So the consent prompt was appearing exactly
 * as designed, with the orb sitting on top of its buttons — and
 * `FLAG_NOT_TOUCH_MODAL` only passes through touches that land OUTSIDE the card.
 * The buttons were on screen and unpressable, which is why the only way through
 * was the notification.
 *
 * So a prompt has to be able to say "I need the screen", and the surfaces have
 * to hear it. That is all this is: a count of live prompts and a list of
 * listeners. It lives in `ui` beside the two bridges because they are what
 * raises a prompt; nothing here knows what a conversation or an overlay is.
 */
object PromptPresence {

    private const val TAG = "JarvisPrompt"

    /**
     * How many prompts are up. A COUNT, not a flag: a permission request raised
     * while a consent prompt is still open is two, and the surface must not
     * come back when the first of them settles.
     */
    private val live = AtomicInteger(0)

    private val listeners = CopyOnWriteArrayList<(Boolean) -> Unit>()

    /** True while anything this app raised is waiting for the user. */
    val anyUp: Boolean get() = live.get() > 0

    /**
     * Called by a bridge as it raises a prompt, and again as it settles.
     *
     * Every `raised()` must be paired with exactly one `settled()`, including on
     * the failure paths — a leaked `raised` leaves the orb hidden and the
     * conversation held for the life of the process, which is a worse bug than
     * the one this fixes. Both bridges do it from a `finally`.
     */
    fun raised() {
        if (live.incrementAndGet() == 1) notify(true)
    }

    fun settled() {
        // Floor at zero. An unpaired `settled` is a bug, and it must not make
        // the count negative — that would swallow the NEXT prompt's `raised`.
        val now = live.updateAndGet { if (it > 0) it - 1 else 0 }
        if (now == 0) notify(false)
    }

    /**
     * @param listener called with true when the first prompt goes up and false
     *   when the last one comes down. Called on the caller's thread, so a
     *   listener that touches views must post to its own.
     */
    fun addListener(listener: (Boolean) -> Unit) {
        listeners.add(listener)
        // Late subscribers are told the current state rather than waiting for
        // the next edge: a surface that appears while a prompt is already up
        // would otherwise cover it, which is the bug.
        if (anyUp) runCatching { listener(true) }
    }

    fun removeListener(listener: (Boolean) -> Unit) {
        listeners.remove(listener)
    }

    private fun notify(up: Boolean) {
        for (listener in listeners) {
            try {
                listener(up)
            } catch (t: Throwable) {
                // One bad listener must not stop the others, and must never
                // stop a prompt being raised.
                Log.w(TAG, "a prompt-presence listener threw", t)
            }
        }
    }

    /** Test seam. */
    fun reset() {
        live.set(0)
        listeners.clear()
    }
}
