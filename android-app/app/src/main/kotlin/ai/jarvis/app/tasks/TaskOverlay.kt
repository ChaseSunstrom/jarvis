package ai.jarvis.app.tasks

import ai.jarvis.app.ManagementActivity
import ai.jarvis.app.ui.ConsoleTab
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.PromptPresence
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.LinearLayout
import kotlin.math.min

/**
 * A chip that floats over whatever you are doing while Jarvis is working.
 *
 * *"all of this should show as a progress bar/UI visual on the web/mobile app,
 * and as an overlay in the android app if doing a task on the phone"* — the
 * console got `/tasks` and a dock; this is the phone's half, and it is the only
 * surface that can say anything at all while the app is in a pocket and the
 * user is in another app entirely.
 *
 * A `TYPE_APPLICATION_OVERLAY` window, the same mechanism as `AssistOverlay`,
 * with the same two limits: it needs SYSTEM_ALERT_WINDOW, and it is not drawn
 * above the keyguard. Both are why [TaskNotifier] exists beside it — a
 * notification is the route that always works, and this is the one that is
 * good when it is available.
 *
 * ## What it refuses to do
 *
 * **Cover a prompt.** Overlay windows draw above every Activity, so a chip
 * sitting where a consent dialog's buttons are makes those buttons
 * unpressable — the exact defect [PromptPresence] was built after. So it hides
 * itself entirely while anything is asking the user something. A progress chip
 * is never worth being in the way of a question.
 *
 * **Stay up when there is nothing to say.** It attaches when work starts and
 * detaches when the last task has finished and lingered. An always-present
 * chip is furniture; this is a report.
 */
class TaskOverlay(private val context: Context) {

    private val main = Handler(Looper.getMainLooper())
    private var root: ViewGroup? = null
    private var progress: TaskProgressView? = null
    private var unlisten: (() -> Unit)? = null
    private var unwatchPrompts: (() -> Unit)? = null
    private var expiry: Runnable? = null
    /** True while a prompt is up and the chip has stood down for it. */
    private var yielded = false

    /** The attached tree, for the instrumented test. Same seam as AssistOverlay. */
    val rootForTest: ViewGroup? get() = root

    val isShowing: Boolean get() = root != null

    /**
     * Start following the board. Idempotent.
     *
     * Nothing appears until there is something to show, so this is safe to call
     * from a service's `onCreate` — which is where it belongs, because the
     * point is to be watching before the user has any reason to look.
     */
    fun start() {
        if (unlisten != null) return
        val onPrompt: (Boolean) -> Unit = { up ->
            main.post {
                yielded = up
                refresh(TaskWatch.visible())
            }
        }
        PromptPresence.addListener(onPrompt)
        unwatchPrompts = { PromptPresence.removeListener(onPrompt) }
        unlisten = TaskWatch.listen { rows -> main.post { refresh(rows) } }
    }

    fun stop() {
        unlisten?.invoke()
        unlisten = null
        unwatchPrompts?.invoke()
        unwatchPrompts = null
        cancelExpiry()
        detach()
    }

    private fun refresh(rows: List<TaskBoard.Row>) {
        cancelExpiry()
        if (rows.isEmpty() || yielded) {
            detach()
            // Still schedule: a chip that stood down for a prompt must come
            // back, and the rows it will come back to may age out meanwhile.
            if (rows.isNotEmpty()) scheduleExpiry()
            return
        }
        if (!attach()) return
        progress?.render(rows, TaskWatch.headline())
        scheduleExpiry()
    }

    /**
     * One timer at the next expiry, not a tick per second.
     *
     * On a phone that difference is battery, not tidiness: without it the chip
     * would wake the main thread once a second for as long as the process
     * lives, including the overwhelming majority of the time when there is no
     * task at all.
     */
    private fun scheduleExpiry() {
        val left = TaskWatch.nextExpiryMs() ?: return
        val tick = Runnable { refresh(TaskWatch.visible()) }
        expiry = tick
        main.postDelayed(tick, left + EXPIRY_SLACK_MS)
    }

    private fun cancelExpiry() {
        expiry?.let { main.removeCallbacks(it) }
        expiry = null
    }

    private fun attach(): Boolean {
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
            // A refused addView must not take anything down with it: the
            // notification is the route that always works, and it is already up.
            Log.w(TAG, "the task overlay was refused", t)
            progress = null
            false
        }
    }

    private fun detach() {
        val view = root ?: return
        root = null
        progress = null
        val windows = context.getSystemService(Context.WINDOW_SERVICE) as? WindowManager
        try {
            windows?.removeView(view)
        } catch (t: Throwable) {
            Log.d(TAG, "the task overlay window was already gone", t)
        }
    }

    private fun build(): ViewGroup {
        val pad = JarvisUi.dp(context, 12)
        val column = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
            // A panel here, unlike the assist orb: that one is a lit object in
            // a dark room and wants no edge, whereas this is a strip of small
            // text over an arbitrary app and needs a ground to be read against.
            background = GradientDrawable().apply {
                cornerRadius = JarvisUi.dp(context, 10).toFloat()
                setColor(PANEL)
                setStroke(JarvisUi.dp(context, 1), STROKE)
            }
            setOnClickListener { openTasks() }
        }
        val view = TaskProgressView(context)
        progress = view
        column.addView(
            view,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ),
        )
        return column
    }

    /** Tapping the chip opens the console on its task list. */
    private fun openTasks() {
        try {
            context.startActivity(
                ManagementActivity.intent(context, ConsoleTab.TASKS)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (t: Throwable) {
            Log.d(TAG, "could not open the task list", t)
        }
    }

    private fun params(): WindowManager.LayoutParams {
        val screen = context.resources.displayMetrics.widthPixels
        val width = min(screen - JarvisUi.dp(context, 24), JarvisUi.dp(context, MAX_WIDTH_DP))
        return WindowManager.LayoutParams(
            width,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            // NOT_FOCUSABLE so the app behind keeps its keyboard, NOT_TOUCH_MODAL
            // so every touch outside the chip goes where it was aimed. No
            // KEEP_SCREEN_ON and no TURN_SCREEN_ON: unlike a wake word, a
            // background job is not a reason to light up somebody's phone.
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT,
        ).apply {
            // Top, and out of the way. The bottom is where the assist orb goes
            // and where most apps put their primary action.
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            y = JarvisUi.dp(context, TOP_MARGIN_DP)
            windowAnimations = 0
        }
    }

    companion object {
        private const val TAG = "JarvisTaskOverlay"
        private const val MAX_WIDTH_DP = 340
        private const val TOP_MARGIN_DP = 56
        /** So a redraw lands just after the moment a task ages out, not just before. */
        private const val EXPIRY_SLACK_MS = 50L

        private val PANEL = 0xF00A1620.toInt()
        private val STROKE = 0x332FC9F0

        fun canShow(context: Context): Boolean =
            android.provider.Settings.canDrawOverlays(context)
    }
}
