package ai.jarvis.app.ui

/**
 * The colours the floating orb wears, and how fast it moves in each state.
 *
 * Separate from [SiriOrbView] because it is the part worth pinning: "the orb
 * changes colour" is a behaviour a user can see and describe, so it gets a
 * table rather than magic numbers scattered through a `draw` method. Kept free
 * of Android imports so a JVM test can assert the table directly, and mirrored
 * in `android-app/tools/siri_palette_test.py`.
 *
 * The palette is deliberately *not* the arc reactor's. [JarvisOrbView] is one
 * colour per state with rings and ticks around it — an instrument. This is
 * three overlapping colours that drift through each other, which is what makes
 * a Siri-style blob read as alive rather than as a status light. They share a
 * state machine and a cyan identity; they do not share a look.
 */
object SiriPalette {

    /**
     * The same five states [JarvisOrbView.Mode] has, restated without the
     * Android dependency the View drags in. [SiriOrbView.setMode] is the only
     * place the two are mapped, so a mode added there and forgotten here is a
     * compile error rather than a colour that silently never appears.
     */
    enum class Tone {
        IDLE,
        LISTENING,
        THINKING,
        SPEAKING,
        ERROR,
    }

    /**
     * Three drifting blobs, in draw order. Fully opaque ARGB: the view fades
     * them itself, because their alpha is what the microphone level moves.
     *
     * Each triple is one hue family plus two neighbours, never three unrelated
     * hues — three colours far apart on the wheel screen-blend to white and the
     * orb turns into a grey smudge the moment they overlap, which is most of
     * the time.
     */
    fun blobs(tone: Tone): IntArray = when (tone) {
        // Resting: the deep cyan the rest of Jarvis idles at, with an indigo
        // and a teal either side of it so it still drifts while nothing is
        // happening.
        Tone.IDLE -> intArrayOf(0xFF2BB0D8.toInt(), 0xFF3A6FE0.toInt(), 0xFF29D8C0.toInt())
        // Hearing you: the brightest state, and the one the app's accent is.
        Tone.LISTENING -> intArrayOf(0xFF3FD8FF.toInt(), 0xFF5A8CFF.toInt(), 0xFF54FFE0.toInt())
        // Working: amber, pushed toward magenta so it is unmistakably not the
        // listening state at a glance from across a room.
        Tone.THINKING -> intArrayOf(0xFFFF9E2C.toInt(), 0xFFFF5FA2.toInt(), 0xFFC46BFF.toInt())
        // Talking: gold and warm, the colour the reactor speaks in.
        Tone.SPEAKING -> intArrayOf(0xFFFFCF5C.toInt(), 0xFFFF9A3C.toInt(), 0xFFFF7BC0.toInt())
        // Wrong: red, and only red-adjacent, so it cannot be mistaken for
        // "thinking" — which is the failure the amber state would otherwise
        // invite.
        Tone.ERROR -> intArrayOf(0xFFFF6B5C.toInt(), 0xFFE0344B.toInt(), 0xFFFF9A6B.toInt())
    }

    /**
     * The hot centre. Near-white everywhere, tinted toward the state, because a
     * pure-white core reads as a flashlight and a fully saturated one loses the
     * sense of something glowing from inside.
     */
    fun core(tone: Tone): Int = when (tone) {
        Tone.IDLE -> 0xFFDFF6FF.toInt()
        Tone.LISTENING -> 0xFFEBFDFF.toInt()
        Tone.THINKING -> 0xFFFFE9CC.toInt()
        Tone.SPEAKING -> 0xFFFFF3D2.toInt()
        Tone.ERROR -> 0xFFFFD9D2.toInt()
    }

    /** The thin ring at the orb's edge, and the halo bleeding out past it. */
    fun rim(tone: Tone): Int = blobs(tone)[0]

    /**
     * Orbit rate in turns per second for the innermost blob; the others are
     * derived from it in the view. These are the web HUD's per-state pulse
     * rates expressed as rotation — 3.5s idle down to 1.0s thinking — so the
     * phone and the browser feel like the same object.
     */
    fun orbitHz(tone: Tone): Float = when (tone) {
        Tone.IDLE -> 1f / 3.5f
        Tone.LISTENING -> 1f / 1.4f
        Tone.THINKING -> 1f / 1.0f
        Tone.SPEAKING -> 1f / 1.2f
        Tone.ERROR -> 1f / 1.6f
    }

    /** How many blobs [blobs] returns, for callers that allocate up front. */
    const val BLOB_COUNT = 3
}
