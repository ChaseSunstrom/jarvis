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
import ai.jarvis.app.ui.ScreenStates
import ai.jarvis.app.ui.SectionStrip
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
 * ## What the goldens are
 *
 * The instrument in its four states (`orb-*`), because that is the picture
 * the whole look is built around and the one that changed most with
 * Reactor II; the console frame's bar with its brand, readout and underline;
 * the widgets every screen is built from; the held bar; the task overlay; the
 * settings widgets; the voice screen's strip and graph (M61); the four screen
 * states and the section strip (M64); and the generated Compose theme. Each
 * is one nameable moment — the animations are frozen at a fixed time — or it
 * would record the scheduler's mood.
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
            // Through the renderer's palette, as both views read it: at rest
            // the instrument is the accent's, not SiriPalette's indigo.
            blobs = ReactorOrb.Palette.blobs(tone)
            core = ReactorOrb.Palette.core(tone)
            rim = ReactorOrb.Palette.rim(tone)
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
            addView(JarvisUi.screenTitle(context, "Jarvis", "The widgets every screen is built from."))
            addView(JarvisUi.label(context, "STATUS"))
            addView(JarvisUi.hint(context, "Listening for the wake word"))
            addView(JarvisUi.readout(context, "wake 0.62  ·  vad 550 ms"))
            addView(JarvisUi.mono(context, "wake 0.62  ·  vad 550 ms"))
            addView(JarvisUi.spacer(context, JarvisUi.Space.TIGHT))
            addView(
                JarvisUi.statusTag(context, "live", JarvisUi.TAG_LIVE),
                LinearLayout.LayoutParams(
                    android.view.ViewGroup.LayoutParams.WRAP_CONTENT,
                    android.view.ViewGroup.LayoutParams.WRAP_CONTENT,
                )
            )
            addView(JarvisUi.spacer(context, JarvisUi.Space.GAP))
            // The one filled control on a screen, and the quiet one beside it.
            addView(JarvisUi.primary(context, "PAIR") {})
            addView(JarvisUi.spacer(context, JarvisUi.Space.TIGHT))
            addView(JarvisUi.button(context, "SETTINGS") {})
        }
        bitmapOf(column).captureRoboImage(golden("components"))
    }

    /**
     * The bar the console screens share, with HOUSE current and the link up:
     * the brand row, the readout, the tabs and the one underline.
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
        strip.setStatus("LINK OK", ConsoleFrame.Tone.LIVE)
        val holder = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(JarvisUi.BG)
            addView(strip)
            addView(JarvisUi.spacer(activity, JarvisUi.Space.SECTION))
        }
        bitmapOf(holder, width = 1233).captureRoboImage(golden("console-frame"))
    }

    /** The four states every screen owes its user, stacked: loading, empty, error, offline. */
    @Test
    fun `the screen states`() {
        val column = JarvisUi.column(context).apply {
            setBackgroundColor(JarvisUi.BG)
            addView(ScreenStates.loading(context, rows = 3, label = "Loading tasks"))
            addView(JarvisUi.spacer(context, JarvisUi.Space.GAP))
            addView(
                ScreenStates.empty(
                    context,
                    "No tasks have run today",
                    "Ask Jarvis for something, or schedule one.",
                )
            )
            addView(JarvisUi.spacer(context, JarvisUi.Space.GAP))
            addView(
                ScreenStates.error(
                    context,
                    "Couldn't load tasks",
                    "The backend answered 500.",
                ) {}
            )
            addView(JarvisUi.spacer(context, JarvisUi.Space.GAP))
            addView(ScreenStates.offline(context, onReconnect = {}))
        }
        // At a phone's width, as the console frame is: the offline state is a
        // row, and at the sheet's default 300 dp its text folds word by word
        // beside the Reconnect control, which no handset is narrow enough for.
        bitmapOf(column, width = 1233).captureRoboImage(golden("screen-states"))
    }

    /** The section strip, with the second section current. */
    @Test
    fun `the section strip`() {
        val strip = SectionStrip(context, listOf("Server", "Voice", "Listen", "Headset", "Permissions")) {}
        strip.select(1)
        val holder = JarvisUi.column(context).apply {
            setBackgroundColor(JarvisUi.BG)
            addView(
                strip,
                LinearLayout.LayoutParams(
                    android.view.ViewGroup.LayoutParams.WRAP_CONTENT,
                    android.view.ViewGroup.LayoutParams.WRAP_CONTENT,
                )
            )
        }
        bitmapOf(holder).captureRoboImage(golden("section-strip"))
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
            // One panel, hairline rows: the three kinds of row in it.
            addView(
                JarvisUi.rows(
                    context,
                    listOf(
                        JarvisUi.checkRow(
                            context,
                            satisfied = true,
                            essential = true,
                            label = "Microphone",
                            why = "Hearing the wake word.",
                            onClick = null,
                        ),
                        JarvisUi.checkRow(
                            context,
                            satisfied = false,
                            essential = true,
                            label = "Network",
                            why = "Reaching your server at all.",
                            onClick = {},
                        ),
                        JarvisUi.checkRow(
                            context,
                            satisfied = false,
                            essential = false,
                            label = "Notifications",
                            why = "Saying when a job finishes.",
                            onClick = {},
                        ),
                    ),
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

    // --- the voice screen's company (M61): the strip and the graph ---------

    @Test
    fun `the activity strip with a turn's rows`() {
        val rows = ai.jarvis.app.assist.ActivityRows()
        rows.apply("jarvis_tool_started", org.json.JSONObject("""{"name":"get_state","arguments":{"entity_id":"light.hall"},"round":1,"index":0}"""), 1L)
        rows.apply("jarvis_tool_finished", org.json.JSONObject("""{"name":"get_state","round":1,"index":0,"ok":true,"duration_ms":84}"""), 2L)
        rows.apply("state_changed", org.json.JSONObject("""{"entity_id":"sensor.garage_temperature","new_state":{"state":"12.5","attributes":{"friendly_name":"Garage temperature","unit_of_measurement":"°C"}}}"""), 3L)
        rows.apply("vision_look_started", org.json.JSONObject("""{"id":"l1","camera":"Kitchen","question":"anyone there?"}"""), 4L)
        rows.apply("jarvis_notification", org.json.JSONObject("""{"notification":{"id":"n1","title":"Check the oven","kind":"reminder"}}"""), 5L)
        val strip = ai.jarvis.app.ui.ActivityStrip(context)
        strip.render(rows)
        bitmapOf(strip).captureRoboImage(golden("voice-activity"))
    }

    @Test
    fun `the knowledge graph for a small house`() {
        val notes = listOf(
            ai.jarvis.app.assist.KnowledgeGraph.NoteLike("n1", "Boiler service", tags = listOf("house"), links = listOf("Meter readings")),
            ai.jarvis.app.assist.KnowledgeGraph.NoteLike("n2", "Meter readings", tags = listOf("house", "energy")),
            ai.jarvis.app.assist.KnowledgeGraph.NoteLike("n3", "Garden plan", tags = listOf("garden")),
        )
        val memory = listOf(
            ai.jarvis.app.assist.KnowledgeGraph.MemoryLike("m1", "The spare key is under the blue pot", tags = listOf("house")),
            ai.jarvis.app.assist.KnowledgeGraph.MemoryLike("m2", "Boiler serviced 2026-08-26", tags = listOf("energy")),
        )
        val (nodes, edges) = ai.jarvis.app.assist.KnowledgeGraph.build(notes, memory)
        val graph = ai.jarvis.app.ui.KnowledgeGraphView(context)
        graph.render(nodes, edges)
        graph.pulse(listOf("note:n1"))
        bitmapOf(graph, height = 420).captureRoboImage(golden("voice-graph"))
    }
}
