package ai.jarvis.app.assist

import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.SiriOrbView
import android.content.Context
import android.graphics.PixelFormat
import android.graphics.Typeface
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
    private var orb: SiriOrbView? = null
    private var caption: TextView? = null
    private var transcript: TextView? = null
    private var response: TextView? = null

    val isShowing: Boolean get() = root != null

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

    fun detach() {
        val view = root ?: return
        root = null
        // All of them, not just the orb: a late callback from a conversation
        // that is still winding down would otherwise write into views that are
        // no longer on screen and keep the whole tree alive.
        orb = null
        caption = null
        transcript = null
        response = null
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
        caption?.setTextColor(mode.color)
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

    // --- construction --------------------------------------------------------

    private fun build(): ViewGroup {
        val pad = JarvisUi.dp(context, 8)
        val column = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(pad, pad, pad, pad)
            // NO background. This was a panel — a dark rounded card with a cyan
            // stroke — and it read as exactly what it was: a box with an orb
            // inside it, sitting on someone's home screen. The orb is the
            // surface; anything drawn behind it is a frame around the thing
            // people actually wanted. Legibility comes from the orb's own glow
            // and from a shadow on the text, not from a slab.
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

        val captionView = TextView(context).apply {
            text = "LISTENING"
            setTextColor(JarvisOrbView.Mode.LISTENING.color)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            letterSpacing = 0.2f
            typeface = Typeface.MONOSPACE
            gravity = Gravity.CENTER
            setPadding(0, JarvisUi.dp(context, 8), 0, 0)
        }
        caption = captionView
        legible(captionView)
        column.addView(captionView, fullWidth())

        val transcriptView = JarvisUi.transcriptView(context).apply {
            maxLines = 3
            ellipsize = TextUtils.TruncateAt.END
            visibility = View.GONE
            setPadding(0, JarvisUi.dp(context, 10), 0, 0)
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
            JarvisUi.dp(context, 6).toFloat(),
            0f,
            JarvisUi.dp(context, 1).toFloat(),
            0xF0000308.toInt(),
        )
    }

    private fun params(): WindowManager.LayoutParams {
        val screen = context.resources.displayMetrics.widthPixels
        val width = min(screen - JarvisUi.dp(context, 32), JarvisUi.dp(context, 340))
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
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            y = JarvisUi.dp(context, 72)
            windowAnimations = 0
        }
    }

    companion object {
        private const val TAG = "JarvisOverlay"

        /** Side of the orb's slot in the card. */
        private const val ORB_DP = 132

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
