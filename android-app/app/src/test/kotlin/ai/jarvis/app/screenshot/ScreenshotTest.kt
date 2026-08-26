package ai.jarvis.app.screenshot

import android.app.Activity
import android.graphics.Bitmap
import android.graphics.Canvas
import android.view.View
import android.widget.LinearLayout
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import androidx.test.core.app.ApplicationProvider
import ai.jarvis.app.tasks.TaskBoard
import ai.jarvis.app.tasks.TaskProgressView
import ai.jarvis.app.ui.ConsoleFrame
import ai.jarvis.app.ui.ConsoleTab
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.ReactorOrb
import ai.jarvis.app.ui.SiriPalette
import ai.jarvis.app.ui.theme.JarvisTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * What the phone LOOKS like, on a machine with no phone.
 *
 * Robolectric runs the real Android framework on the JVM — real resources, a
 * real layout pass, a real Canvas — and Roborazzi turns the result into a PNG
 * and compares it with the golden beside this file. `verifyRoborazziDebug`
 * fails on a difference.
 *
 * ## Why this is worth having when the goldens cannot be looked at by a person
 *
 * They can. They are PNGs in the repository, reviewed in a diff like anything
 * else. What this catches is the class of change nobody notices until a device
 * is in front of them: a token that moved, a padding that collapsed, a colour
 * that stopped being read from `design/tokens.json` and became a literal.
 * `design_token_test.py` proves the *values* agree; this proves they are what
 * actually gets drawn.
 *
 * ## What the ten goldens are
 *
 * The instrument in its four states (`orb-*`), because that is the picture
 * the whole look is built around and the one that changed most with
 * Reactor II; the console frame's strip with its underline; the widgets every
 * screen is built from; the held bar; the task overlay; the settings widgets;
 * and the generated Compose theme. Each is one nameable moment — the
 * animations are frozen at a fixed time — or it would record the scheduler's
 * mood.
 *
 * ## What it is not
 *
 * It is not a device test. Robolectric's Canvas is not a phone's GPU, text
 * shaping is not identical to a real one, and `docs/ANDROID_DEVICE_TESTS.md`
 * lists what still needs hardware. `GraphicsMode.Mode.NATIVE` is what makes the
 * pixels real enough to compare at all — without it every drawing test records
 * a blank frame and passes for the worst possible reason.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class ScreenshotTest {

    @get:Rule
    val compose = createComposeRule()

    private val context = ApplicationProvider.getApplicationContext<android.content.Context>()

    private fun golden(name: String) = "src/test/screenshots/$name.png"

    /**
     * A view, laid out and drawn onto a bitmap.
     *
     * Drawn rather than handed to `View.captureRoboImage()`, which wants the
     * view attached to an Activity ("View should have Activity") — and these
     * are the pieces screens are BUILT from, so hanging each one off a host
     * activity would be testing the activity. Drawing them is what the real
     * one does with them anyway.
     */
    private fun bitmapOf(view: View, width: Int = 900, height: Int = 0): Bitmap {
        measured(view, width, height)
        val bitmap = Bitmap.createBitmap(
            view.measuredWidth.coerceAtLeast(1),
            view.measuredHeight.coerceAtLeast(1),
            Bitmap.Config.ARGB_8888,
        )
        val canvas = Canvas(bitmap)
        canvas.drawColor(JarvisUi.BG)
        view.draw(canvas)
        return bitmap
    }

    /** Lay a view out at a fixed size, so a golden is not a race with wrap-content. */
    private fun measured(view: View, width: Int = 900, height: Int = 0): View {
        val heightSpec =
            if (height > 0) View.MeasureSpec.makeMeasureSpec(height, View.MeasureSpec.EXACTLY)
            else View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
        view.measure(View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY), heightSpec)
        view.layout(0, 0, view.measuredWidth, view.measuredHeight)
        return view
    }

    // --- the instrument, in the four states a person can tell apart ---------

    /**
     * The reactor at one fixed instant.
     *
     * `time` is the renderer's clock: the blades, coil, irises, glint and idle
     * breath are all read off it against the motion tokens, so pinning it
     * pins every one of them. `level` is what a microphone would have said.
     */
    private fun orb(tone: SiriPalette.Tone, level: Float, turbulence: Boolean): Bitmap {
        val size = 480
        val bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        canvas.drawColor(JarvisUi.BG)
        val orb = ReactorOrb(density = 3f)
        val frame = ReactorOrb.Frame().apply {
            cx = size / 2f
            cy = size / 2f
            radius = size / 2.6f
            alpha = 1f
            this.level = level
            // Fixed, not free-running: a golden of an animation has to be of
            // one nameable moment, or it records the scheduler's mood.
            time = 1.2f
            phase = 1.2f
            spinDeg = 42f
            blobs = SiriPalette.blobs(tone)
            core = SiriPalette.core(tone)
            rim = SiriPalette.rim(tone)
            idle = tone == SiriPalette.Tone.IDLE
            rimAlpha =
                if (tone == SiriPalette.Tone.LISTENING || tone == SiriPalette.Tone.SPEAKING) {
                    ReactorOrb.RIM_ALPHA_LIT
                } else {
                    ReactorOrb.RIM_ALPHA_REST
                }
            this.turbulence = turbulence
            maxRadius = size / 2f
            settleRings()
        }
        orb.draw(canvas, frame)
        return bitmap
    }

    @Test
    fun `the orb idle`() {
        orb(SiriPalette.Tone.IDLE, level = 0f, turbulence = false)
            .captureRoboImage(golden("orb-idle"))
    }

    @Test
    fun `the orb listening`() {
        orb(SiriPalette.Tone.LISTENING, level = 0.35f, turbulence = false)
            .captureRoboImage(golden("orb-listening"))
    }

    @Test
    fun `the orb thinking`() {
        orb(SiriPalette.Tone.THINKING, level = 0.05f, turbulence = true)
            .captureRoboImage(golden("orb-thinking"))
    }

    @Test
    fun `the orb speaking`() {
        orb(SiriPalette.Tone.SPEAKING, level = 0.6f, turbulence = false)
            .captureRoboImage(golden("orb-speaking"))
    }

    // --- the widgets every screen is built from ------------------------------

    @Test
    fun `the component sheet`() {
        val column = JarvisUi.column(context).apply {
            setBackgroundColor(JarvisUi.BG)
            addView(JarvisUi.title(context, "Jarvis"))
            addView(JarvisUi.label(context, "STATUS"))
            addView(JarvisUi.hint(context, "Listening for the wake word"))
            addView(JarvisUi.mono(context, "wake 0.62  ·  vad 550 ms"))
            addView(JarvisUi.spacer(context, JarvisUi.Space.GAP))
            // The one filled control on a screen, and the quiet one beside it.
            addView(JarvisUi.primary(context, "PAIR") {})
            addView(JarvisUi.spacer(context, JarvisUi.Space.TIGHT))
            addView(JarvisUi.button(context, "SETTINGS") {})
        }
        bitmapOf(column).captureRoboImage(golden("components"))
    }

    /**
     * The strip the console screens share, with HOUSE current.
     *
     * The frame wants an Activity (it starts one for PHONE), so this is the
     * one golden hung off a host. A bare [Activity], not one of ours: what is
     * being pictured is the strip, and the app's activities each bring a
     * service binding or a channel the picture does not need.
     */
    @Test
    fun `the console frame`() {
        val activity = Robolectric.buildActivity(Activity::class.java).setup().get()
        val strip = ConsoleFrame.tabBar(activity, ConsoleTab.DEFAULT) {}
        val holder = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(JarvisUi.BG)
            addView(strip)
            addView(JarvisUi.spacer(activity, JarvisUi.Space.SECTION))
        }
        bitmapOf(holder, width = 1233).captureRoboImage(golden("console-frame"))
    }

    @Test
    fun `the approval banner`() {
        val holder = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(JarvisUi.BG)
            val p = JarvisUi.dp(context, JarvisUi.Space.GAP)
            setPadding(p, p, p, p)
        }
        holder.addView(
            JarvisUi.banner(
                context,
                "Unlock the front door? Nothing has happened yet.",
                "APPROVE",
            ) {}
        )
        bitmapOf(holder).captureRoboImage(golden("approval-banner"))
    }

    @Test
    fun `the task overlay`() {
        val view = TaskProgressView(context)
        view.render(
            listOf(
                TaskBoard.Row(
                    id = "a",
                    title = "Research the boiler pressure range",
                    kind = "research",
                    status = TaskBoard.Status.RUNNING,
                    fraction = 0.4,
                    detail = "reading the handbook",
                    doneSteps = 2,
                    totalSteps = 5,
                ),
                TaskBoard.Row(
                    id = "b",
                    title = "Fix the failing tests",
                    kind = "code",
                    status = TaskBoard.Status.DONE,
                    fraction = 1.0,
                    result = "3 tests pass on jarvis/20260825-a1b2",
                    doneSteps = 4,
                    totalSteps = 4,
                ),
            ),
            summary = "2 jobs",
        )
        view.visibility = View.VISIBLE
        bitmapOf(view).captureRoboImage(golden("task-overlay"))
    }

    /**
     * The settings screen's parts: a labelled field, a secret one, a chooser,
     * and the two kinds of check row. The screen itself binds to the
     * service; its widgets are what a golden can hold still.
     */
    @Test
    fun `the settings fields`() {
        val column = JarvisUi.column(context).apply {
            setBackgroundColor(JarvisUi.BG)
            addView(JarvisUi.label(context, "SERVER"))
            addView(JarvisUi.field(context, "https://jarvis.local", "https://jarvis.tail1234.ts.net"))
            addView(JarvisUi.spacer(context, JarvisUi.Space.TIGHT))
            addView(JarvisUi.field(context, "token", "hunter2", secret = true))
            addView(JarvisUi.spacer(context, JarvisUi.Space.GAP))
            addView(JarvisUi.label(context, "WAKE WORD"))
            addView(JarvisUi.chooser(context, "Wake word", listOf("hey jarvis", "ok jarvis"), 0) {})
            addView(JarvisUi.spacer(context, JarvisUi.Space.GAP))
            addView(JarvisUi.label(context, "CHECKS"))
            addView(
                JarvisUi.checkRow(
                    context,
                    satisfied = true,
                    essential = true,
                    label = "Microphone",
                    why = "Hearing the wake word.",
                    onClick = null,
                )
            )
            addView(JarvisUi.spacer(context, JarvisUi.Space.TIGHT))
            addView(
                JarvisUi.checkRow(
                    context,
                    satisfied = false,
                    essential = false,
                    label = "Notifications",
                    why = "Saying when a job finishes.",
                    onClick = {},
                )
            )
            addView(JarvisUi.spacer(context, JarvisUi.Space.GAP))
            addView(JarvisUi.primary(context, "SAVE") {})
        }
        bitmapOf(column).captureRoboImage(golden("settings-fields"))
    }

    // --- and the generated Compose theme -------------------------------------

    @Test
    fun `the generated theme`() {
        compose.setContent {
            JarvisTheme {
                Column(
                    Modifier
                        .fillMaxWidth()
                        .background(MaterialTheme.colorScheme.background)
                        .padding(16.dp)
                ) {
                    Text("JARVIS", style = MaterialTheme.typography.titleLarge)
                    Text("Reactor II", style = MaterialTheme.typography.bodyMedium)
                    Text(
                        "accent",
                        color = MaterialTheme.colorScheme.primary,
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
        }
        compose.onRoot().captureRoboImage(golden("theme-panel"))
    }
}
