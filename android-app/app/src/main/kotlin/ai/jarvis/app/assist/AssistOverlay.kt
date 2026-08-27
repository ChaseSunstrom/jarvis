package ai.jarvis.app.assist

import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.ReadabilityScrim
import ai.jarvis.app.ui.SiriOrbView
import android.content.Context
import android.graphics.PixelFormat
import android.os.Build
import android.text.TextUtils
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.min
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * Jarvis, on screen, over whatever you were doing.
 *
 * Until this existed the wake word could only reach the user through
 * [ai.jarvis.app.JarvisAssistActivity] — an Activity, which means a task switch,
 * which means Android 12+ silently drops it when the app is in the background
 * and the conversation arrives as a notification the user has to tap. That is
 * why the reported symptom was "it can speak back to me, but there is no
 * overlay on my screen": the voice path worked and the visible half did not
 * exist.
 *
 * A `TYPE_APPLICATION_OVERLAY` window is the thing that actually floats. It
 * needs SYSTEM_ALERT_WINDOW ("display over other apps"), which is a Settings
 * trip the user has to make, so [canShow] is checked before every attempt and
 * the caller keeps the old Activity path as the fallback — see
 * [WakeWordService.onWakeWord].
 *
 * **Two deliberate limits.**
 *  * Overlay windows are not shown above the keyguard, by design and with no
 *    way around it. On a locked phone the caller uses the full-screen intent
 *    instead, which is the platform's own mechanism for that case.
 *  * The window is sized to the card, not to the screen. A full-width window
 *    with FLAG_NOT_TOUCH_MODAL still swallows every touch inside its own
 *    bounds, so a screen-sized overlay would eat taps on the app behind it
 *    across the whole display. Sizing it to what is actually drawn means only
 *    the card itself is in the way, and tapping it dismisses.
 */
class AssistOverlay(
    private val context: Context,
    /** The user tapped the card; the caller should end the conversation. */
    private val onDismiss: () -> Unit,
) {

    private var root: ViewGroup? = null

    /**
     * Whether the card is currently worn as a badge, out of a prompt's way.
     *
     * Held so [setHiddenForPrompt] is idempotent: `PromptPresence` fires on the
     * 0->1 and 1->0 edges, but `WakeWordService` also re-asserts the state when
     * a conversation starts or ends underneath a prompt that is already up, and
     * a second `updateViewLayout` with identical params is a visible flicker on
     * the one surface whose job is to look alive.
     */
    private var compact = false
    private var orb: SiriOrbView? = null
    private var caption: TextView? = null
    private var transcript: TextView? = null
    private var response: TextView? = null
    private var toolActivity: ToolActivityView? = null

    val isShowing: Boolean get() = root != null

    /**
     * The attached view tree, for the instrumented test.
     *
     * A seam rather than a feature. Whether `TYPE_APPLICATION_OVERLAY` is
     * accepted, and whether what it accepts has a size, is not knowable by
     * reading code — it depends on an appop, a window type and a set of flags —
     * and this surface has now been reported broken three times. `AssistOverlayTest`
     * asks the real WindowManager, and needs a handle on what it added.
     */
    val rootForTest: ViewGroup? get() = root

    /**
     * Put the orb on screen.
     *
     * @return false if it could not be shown — no permission, or the window
     *   manager refused — so the caller can fall back rather than assume a
     *   surface that is not there.
     */
    fun attach(): Boolean {
        if (root != null) return true
        if (!canShow(context)) return false

        val windows = context.getSystemService(Context.WINDOW_SERVICE) as? WindowManager
            ?: return false
        val view = build()
        return try {
            windows.addView(view, params())
            root = view
            true
        } catch (t: Throwable) {
            // A refused addView is the one failure mode that must not take the
            // conversation down with it: the caller still has the notification
            // path, and a wake word that produces nothing at all is the bug
            // this whole class exists to fix.
            Log.w(TAG, "the overlay window was refused", t)
            false
        }
    }

    /**
     * Get out of the way of a prompt — by moving, not by disappearing.
     *
     * A `TYPE_APPLICATION_OVERLAY` window is drawn **above every Activity**, so
     * while this card is up it is on top of `ApprovalActivity` — and that screen
     * puts DENY and APPROVE at the end of its column, which is where this card
     * sits: 340dp wide, anchored 72dp off the bottom. `FLAG_NOT_TOUCH_MODAL`
     * passes through only the touches that land OUTSIDE the card, so the two
     * buttons were on screen and unpressable. That was the report: *"it still
     * forces me to click on the tool call to approve"*.
     *
     * The first fix for that was `View.GONE`, and it worked by making the two
     * surfaces mutually exclusive: any prompt going up took the orb off the
     * screen entirely. So Jarvis asking you something meant Jarvis vanishing
     * while it asked, and the conversation you were having — still running,
     * still listening — had no visible surface at all. The prompt and the orb
     * could not coexist, which is the defect this replaces.
     *
     * They can. The z-order cannot be argued with and the geometry can: the
     * card collapses to a small badge pinned to the TOP of the screen, clear of
     * the prompt's buttons, and stays visible and animating. `FLAG_NOT_TOUCH_MODAL`
     * then does the right thing on its own — every touch aimed at the prompt
     * lands outside the badge and goes through.
     *
     * Never [detach]: that would drop the view tree and the conversation's
     * callbacks with it, and this has to be reversible — the card comes back
     * the moment the prompt is answered, with the turn still running underneath.
     */
    fun setHiddenForPrompt(hidden: Boolean) {
        val view = root ?: return
        if (compact == hidden) return
        compact = hidden
        // The rows that make this a card rather than a badge. The orb itself
        // stays: it is the thing that says Jarvis is still listening, which is
        // the whole reason for not hiding the window.
        val rows = if (hidden) View.GONE else View.VISIBLE
        caption?.visibility = rows
        transcript?.visibility = rows
        response?.visibility = rows
        toolActivity?.visibility = rows
        // The orb has to shrink with the window. It is laid out at ORB_DP
        // (176dp) inside a card 340dp wide; left at that size in an 88dp badge
        // it would simply be clipped, and a clipped arc reactor reads as a
        // rendering fault rather than as a smaller Jarvis.
        orb?.let { ball ->
            val size = JarvisUi.dp(context, if (hidden) BADGE_ORB_DP else ORB_DP)
            ball.layoutParams = ball.layoutParams?.also {
                it.width = size
                it.height = size
            } ?: LinearLayout.LayoutParams(size, size)
            ball.requestLayout()
        }
        view.visibility = View.VISIBLE
        val windows = context.getSystemService(Context.WINDOW_SERVICE) as? WindowManager
        try {
            windows?.updateViewLayout(view, params(compact = hidden))
        } catch (t: Throwable) {
            // A window the platform has already taken away. Falling back to the
            // old behaviour is worse than leaving it where it is: the prompt is
            // what matters here, and it is on screen either way.
            Log.w(TAG, "could not move the overlay clear of the prompt", t)
        }
    }

    fun detach() {
        val view = root ?: return
        root = null
        // So the next conversation opens as a card. Without this a turn that
        // ended while a prompt was up would come back as a badge, and
        // `setHiddenForPrompt(false)` would think it had nothing to do.
        compact = false
        // All of them, not just the orb: a late callback from a conversation
        // that is still winding down would otherwise write into views that are
        // no longer on screen and keep the whole tree alive.
        orb = null
        caption = null
        transcript = null
        response = null
        toolActivity = null
        val windows = context.getSystemService(Context.WINDOW_SERVICE) as? WindowManager
        try {
            windows?.removeView(view)
        } catch (t: Throwable) {
            Log.w(TAG, "the overlay window was already gone", t)
        }
    }

    // --- what the conversation drives ---------------------------------------

    fun setMode(mode: JarvisOrbView.Mode, label: String) {
        orb?.setMode(mode)
        caption?.text = label
    }

    fun setAmplitude(level: Float) {
        orb?.setAmplitude(level)
    }

    fun setTranscript(text: String) {
        transcript?.text = text
        transcript?.visibility = if (text.isEmpty()) View.GONE else View.VISIBLE
    }

    fun setResponse(text: String) {
        response?.text = text
        response?.visibility = if (text.isEmpty()) View.GONE else View.VISIBLE
    }

    /**
     * What the turn is touching, under the orb.
     *
     * The overlay is the surface that is up when a wake word starts a turn from
     * a locked phone on a shelf — which is exactly when nobody can see a console
     * — so it is the surface that most needs to say "this just unlocked the
     * front door" rather than only speaking a sentence about it.
     */
    fun setTools(run: ToolRun) {
        toolActivity?.render(run)
    }

    // --- construction --------------------------------------------------------

    private fun build(): ViewGroup {
        // Generous, because the scrim has to reach zero before the window's
        // edge does — see ReadabilityScrim. Padding is what buys it that room.
        val pad = JarvisUi.dp(context, JarvisUi.Size.GUTTER)
        val column = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(pad, pad, pad, pad)
            // Something to read against. NOT a panel: a radial gradient with no
            // edge of its own, densest behind the orb and gone before the
            // window ends. See ReadabilityScrim for why the two previous
            // attempts at this — both rounded cards with a cyan stroke — were
            // removed, and why blurring alone does not do it (blurring white
            // gives white).
            background = ReadabilityScrim()
            setOnClickListener { onDismiss() }
        }

        // Locals first, fields second. `addView` takes a non-null View, and
        // handing it the nullable field would be both a compile risk and a
        // needless null check on a value that was just constructed.
        val orbView = SiriOrbView(context)
        orb = orbView
        column.addView(
            orbView,
            LinearLayout.LayoutParams(JarvisUi.dp(context, ORB_DP), JarvisUi.dp(context, ORB_DP))
        )

        // The console's `.cap`: the chrome face, the smallest step, wide
        // tracking, always the accent at rest — the reactor above it carries
        // the state's colour. It was tinted per state and tracked by hand.
        val captionView = TextView(context).apply {
            text = "LISTENING"
            setTextColor(JarvisTokens.Color.ACCENT_DEEP)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
            letterSpacing = JarvisUi.TRACK_WIDE
            typeface = JarvisUi.MONO_FACE
            gravity = Gravity.CENTER
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.STEP), 0, 0)
        }
        caption = captionView
        legible(captionView)
        column.addView(captionView, fullWidth())

        // Between the caption and the words: what Jarvis is DOING sits above
        // what it is saying, because the doing is what a person in the room
        // needs to be able to stop.
        val tools = ToolActivityView(context).apply {
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.ROW), 0, 0)
        }
        toolActivity = tools
        column.addView(tools, fullWidth())

        val transcriptView = JarvisUi.transcriptView(context).apply {
            // Brighter than the shared transcript colour, which is DIM.
            // This surface floats over whatever the user was looking at, and
            // DIM reaches WCAG AA there only under a nearly opaque scrim —
            // i.e. under the dark card this overlay has twice had removed.
            // See overlay_scrim_test.py, which measures exactly that.
            setTextColor(JarvisUi.TEXT)
            maxLines = 3
            ellipsize = TextUtils.TruncateAt.END
            visibility = View.GONE
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.ROW), 0, 0)
        }
        val responseView = JarvisUi.responseView(context).apply {
            maxLines = 4
            ellipsize = TextUtils.TruncateAt.END
            visibility = View.GONE
        }
        legible(transcriptView)
        legible(responseView)
        transcript = transcriptView
        response = responseView
        column.addView(transcriptView, fullWidth())
        column.addView(responseView, fullWidth())

        return column
    }

    private fun fullWidth() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT,
    )

    /**
     * What replaces the card: a hard shadow under the glyphs.
     *
     * Text with no ground behind it has to survive being drawn over a white
     * app, a photo, or a video. A dark blurred shadow does that at a fraction
     * of the visual weight of a panel, and it is what every system overlay on
     * the platform does for the same reason.
     */
    private fun legible(view: TextView) {
        view.setShadowLayer(
            JarvisUi.dp(context, JarvisUi.Space.SNUG).toFloat(),
            0f,
            JarvisUi.dp(context, JarvisUi.Space.HAIRLINE).toFloat(),
            JarvisTokens.Color.SCRIM_HEAVY,
        )
    }

    /**
     * @param compact the badge form, worn while a prompt is on screen. Narrow
     *   enough to be clear of a consent screen's buttons and pinned to the top
     *   rather than the bottom, so `FLAG_NOT_TOUCH_MODAL` lets every touch
     *   aimed at the prompt through. See [setHiddenForPrompt].
     */
    private fun params(compact: Boolean = false): WindowManager.LayoutParams {
        val screen = context.resources.displayMetrics.widthPixels
        val width =
            if (compact) JarvisUi.dp(context, BADGE_DP)
            else min(screen - JarvisUi.dp(context, JarvisUi.Size.SHEET), JarvisUi.dp(context, JarvisUi.Size.PANEL_MAX))
        return WindowManager.LayoutParams(
            width,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            // NOT_FOCUSABLE so the app behind keeps its keyboard and its focus —
            // an overlay that steals focus while somebody is typing is worse
            // than no overlay. NOT_TOUCH_MODAL so touches outside the card go
            // where they were aimed.
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                // Screen blending inside a saveLayer on a software canvas is
                // slow enough to drop frames on the exact surface whose whole
                // job is to look alive.
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED or
                // A wake word is for the phone you are NOT holding, so the
                // screen is usually off and the keyguard usually up. These are
                // deprecated for Activities — replaced by setShowWhenLocked()
                // and setTurnScreenOn(), which only exist on Activity — and are
                // still the only way to say it for a window put up by a
                // service. Where a build ignores them the overlay simply sits
                // behind the keyguard, which is why WakeWordService posts the
                // full-screen intent as well whenever the phone is locked.
                @Suppress("DEPRECATION")
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                @Suppress("DEPRECATION")
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
            PixelFormat.TRANSLUCENT,
        ).apply {
            // Top while a prompt is up. A consent screen puts its buttons at the
            // end of its column, so the badge goes to the opposite end of the
            // screen rather than merely somewhere smaller.
            gravity =
                if (compact) Gravity.TOP or Gravity.CENTER_HORIZONTAL
                else Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            y = JarvisUi.dp(context, if (compact) BADGE_MARGIN_DP else 72)
            windowAnimations = 0
            // No blur in the badge form: it would dim the prompt the user is
            // being asked to read.
            if (!compact) blurBehind(this)
        }
    }

    /**
     * Blur the app behind the orb, where the platform can.
     *
     * Cross-window blur is API 31 and up, and even there it is a request rather
     * than a setting: `isCrossWindowBlurEnabled` goes false under battery
     * saver, when the developer option is off, and on hardware that cannot do
     * it. So this is never the only thing making the overlay readable —
     * [ReadabilityScrim] is, and it is drawn on every build. Blur is the part
     * that makes it look like Jarvis rather than like a dark patch.
     *
     * FLAG_DIM_BEHIND is deliberately NOT set. It dims the entire screen behind
     * the window, and this overlay comes up unbidden over whatever the user is
     * doing — darkening a whole map or a video because a wake word fired is a
     * bigger interruption than the popup itself. The blur and the scrim are
     * both bounded by the card.
     */
    private fun blurBehind(params: WindowManager.LayoutParams) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        params.flags = params.flags or WindowManager.LayoutParams.FLAG_BLUR_BEHIND
        params.blurBehindRadius = JarvisUi.dp(context, BLUR_DP)
    }

    companion object {
        private const val TAG = "JarvisOverlay"

        /**
         * Blur radius behind the card, in dp.
         *
         * Enough that text behind it stops being readable as text — which is
         * the actual requirement, since a legible sentence behind a legible
         * sentence is what makes the overlay hard to read — and not so much
         * that the phone stops looking like the phone.
         */
        private const val BLUR_DP = 28

        /**
         * Side of the orb's slot in the card.
         *
         * Raised from 132 when the floating orb became the whole arc reactor
         * rather than a bare ball. The renderer sizes the glowing centre so its
         * outermost ring still fits — `half / (OUTER_FACTOR * maxScale)` — so
         * the same slot that held a 132dp ball now has to hold a 132dp
         * *assembly*, and the ball inside it would have come out at a third of
         * that. The card is 340dp wide; this leaves a comfortable margin.
         */
        private const val ORB_DP = 176

        /**
         * The badge worn while a prompt is on screen. See [setHiddenForPrompt].
         *
         * Wide enough that the orb is still legibly the orb — it is the only
         * thing still saying Jarvis is listening — and narrow enough that a
         * consent screen's buttons are nowhere near it. It sits at the top,
         * the prompt's buttons are at the bottom, and `FLAG_NOT_TOUCH_MODAL`
         * does the rest.
         */
        private const val BADGE_DP = 88
        private const val BADGE_MARGIN_DP = 28

        /**
         * The orb inside the badge. Smaller than [ORB_DP] by the same reasoning
         * that made ORB_DP large: the ball is drawn inside its own bounds with
         * room for the ring and the glow, so the box has to be the assembly's
         * size rather than the ball's. 64 in an 88dp window leaves the margin
         * that keeps the glow from being cut off at the window edge.
         */
        private const val BADGE_ORB_DP = 64

        /**
         * Whether "display over other apps" is granted.
         *
         * `Settings.canDrawOverlays` and nothing else: the manifest permission
         * is declared but that only makes the toggle appear in Settings, and
         * checking `checkSelfPermission` for SYSTEM_ALERT_WINDOW returns
         * GRANTED on some ROMs while `addView` still throws.
         */
        fun canShow(context: Context): Boolean =
            android.provider.Settings.canDrawOverlays(context)
    }
}
