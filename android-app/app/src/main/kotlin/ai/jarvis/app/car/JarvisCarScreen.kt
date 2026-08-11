package ai.jarvis.app.car

import ai.jarvis.app.assist.JarvisConversation
import ai.jarvis.app.assist.ToolRun
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisOrbView
import android.Manifest
import android.content.pm.PackageManager
import androidx.car.app.CarContext
import androidx.car.app.CarToast
import androidx.car.app.Screen
import androidx.car.app.model.Action
import androidx.car.app.model.ActionStrip
import androidx.car.app.model.CarColor
import androidx.car.app.model.CarIcon
import androidx.car.app.model.Pane
import androidx.car.app.model.PaneTemplate
import androidx.car.app.model.Row
import androidx.car.app.model.Template
import androidx.core.graphics.drawable.IconCompat
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner

/**
 * What the head unit shows: the orb, what you said, and what Jarvis said back.
 *
 * ## Why a Pane and not a List
 *
 * A `PaneTemplate` is the one template that will show a large image beside a
 * few rows of text, which is the shape of this: the orb IS the state readout,
 * the way it is in the browser and on the phone, and the two lines under it are
 * the conversation. A list would make Jarvis look like a menu of things to
 * pick, which is the opposite of what it is.
 *
 * ## The refresh budget, which shapes everything here
 *
 * A car host limits how many times an app may push a new template — the whole
 * point being that a screen changing while somebody is driving is a screen
 * being read instead of the road. So this does not stream: it invalidates when
 * the STATE changes or when a line of text actually settles, and
 * [lastRendered] makes an identical template a no-op rather than a spent
 * refresh. Streaming deltas, which is what the phone and the browser do, would
 * burn the budget in one sentence and then the screen would stop updating at
 * the moment it mattered.
 *
 * ## The microphone
 *
 * Not opened by arriving here. [TALK] starts a conversation, the wake word
 * still works from the phone, and leaving the screen stops anything this
 * screen started — a car that disconnects must not leave a microphone open.
 */
class JarvisCarScreen(carContext: CarContext) :
    Screen(carContext), JarvisConversation.Ui, DefaultLifecycleObserver {

    private val config = JarvisConfig(carContext.applicationContext)
    private var convo: JarvisConversation? = null

    private var mode = JarvisOrbView.Mode.IDLE
    private var stateLabel = "STANDBY"
    private var transcript = ""
    private var response = ""
    private var level = 0f

    /**
     * The last thing actually handed to the host.
     *
     * Not a nicety. `invalidate()` costs one of a small number of refreshes,
     * and the conversation calls back on every amplitude sample — so without
     * this, a single spoken sentence would exhaust the budget and the reply
     * would never appear.
     */
    private var lastRendered: String? = null

    init {
        lifecycle.addObserver(this)
    }

    override fun onStop(owner: LifecycleOwner) {
        // The screen is gone; nothing it started may outlive it.
        stopTalking()
    }

    override fun onDestroy(owner: LifecycleOwner) {
        stopTalking()
    }

    override fun onGetTemplate(): Template {
        val pane = Pane.Builder()
            .addRow(
                Row.Builder()
                    .setTitle(stateLabel)
                    .addText(if (transcript.isBlank()) SUBTITLE_IDLE else transcript)
                    .build()
            )
            .also { builder ->
                if (response.isNotBlank()) {
                    builder.addRow(
                        Row.Builder().setTitle(RESPONSE_TITLE).addText(response).build()
                    )
                }
                if (!config.isConfigured) {
                    builder.addRow(
                        Row.Builder()
                            .setTitle(NOT_SET_UP_TITLE)
                            .addText(NOT_SET_UP_BODY)
                            .build()
                    )
                }
            }
            // The orb, drawn by the same renderer the phone uses. `setImage`
            // on a Pane is the large art slot, which is the only place on a
            // template where Jarvis can actually look like Jarvis.
            .setImage(
                CarIcon.Builder(
                    IconCompat.createWithBitmap(CarOrbRenderer.render(mode, ORB_PX, level))
                ).build()
            )
            .addAction(talkAction())
            .build()

        return PaneTemplate.Builder(pane)
            .setTitle(TITLE)
            .setHeaderAction(Action.APP_ICON)
            .setActionStrip(
                ActionStrip.Builder()
                    .addAction(
                        Action.Builder()
                            .setTitle(if (isTalking()) STOP else TALK)
                            .setOnClickListener { toggleTalking() }
                            .build()
                    )
                    .build()
            )
            .build()
    }

    private fun talkAction(): Action =
        Action.Builder()
            .setTitle(if (isTalking()) STOP else TALK)
            .setBackgroundColor(if (isTalking()) CarColor.RED else CarColor.BLUE)
            .setOnClickListener { toggleTalking() }
            .build()

    private fun isTalking(): Boolean = convo?.isRunning == true

    private fun toggleTalking() {
        if (isTalking()) {
            stopTalking()
            return
        }
        if (!config.isConfigured) {
            CarToast.makeText(carContext, NOT_SET_UP_BODY, CarToast.LENGTH_LONG).show()
            return
        }
        // The car host cannot grant this and must not pretend to. RECORD_AUDIO
        // is granted on the phone, and a driver who has not done that gets told
        // so rather than a button that silently does nothing.
        if (carContext.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            CarToast.makeText(carContext, NEEDS_MIC, CarToast.LENGTH_LONG).show()
            return
        }
        transcript = ""
        response = ""
        convo = JarvisConversation(
            carContext.applicationContext,
            config,
            this,
            inactivityMs = CAR_INACTIVITY_MS,
        ).also { it.start() }
        render()
    }

    private fun stopTalking() {
        convo?.stop()
        convo = null
    }

    // --- JarvisConversation.Ui ----------------------------------------------

    override fun onMode(mode: JarvisOrbView.Mode, label: String) {
        this.mode = mode
        stateLabel = label
        render()
    }

    /**
     * Deliberately does nothing.
     *
     * This arrives per audio buffer — tens of times a second — and every one of
     * them would be a template refresh. The orb's swell is a phone and browser
     * affordance; on a head unit the state colour carries the same information
     * without asking the driver to watch something move.
     */
    override fun onAmplitude(level: Float) = Unit

    override fun onTranscript(text: String) {
        transcript = text
        render()
    }

    override fun onResponse(text: String) {
        response = text
        render()
    }

    override fun onError(message: String) {
        stateLabel = "ERROR"
        mode = JarvisOrbView.Mode.ERROR
        response = message
        render()
    }

    override fun onIdle() {
        convo = null
        mode = JarvisOrbView.Mode.IDLE
        stateLabel = "STANDBY"
        render()
    }

    /**
     * Tool activity is not shown here, and that is a decision rather than a gap.
     *
     * The phone and the console list every call as it happens, because somebody
     * looking at them can read it and stop it. A driver cannot, and a list that
     * grows while the car is moving is precisely the thing the refresh budget
     * exists to prevent. What a Tier-3 action still gets is the approval
     * prompt, on the phone, which is where a human has to be anyway.
     */
    override fun onTools(run: ToolRun) = Unit

    /** Push a template only if it would differ from the last one. */
    private fun render() {
        val signature = listOf(mode.name, stateLabel, transcript, response, isTalking()).toString()
        if (signature == lastRendered) return
        lastRendered = signature
        invalidate()
    }

    private companion object {
        const val TITLE = "Jarvis"
        const val TALK = "Talk"
        const val STOP = "Stop"
        const val RESPONSE_TITLE = "Jarvis"
        const val SUBTITLE_IDLE = "Say “Hey Jarvis”, or press Talk."
        const val NOT_SET_UP_TITLE = "Not set up"
        const val NOT_SET_UP_BODY =
            "Open Jarvis on your phone and point it at your server first."
        const val NEEDS_MIC =
            "Jarvis needs the microphone permission. Grant it in the app on your phone."

        /**
         * Square side of the orb bitmap, in pixels.
         *
         * Car displays vary and the host scales what it is given, so this is a
         * size that looks right scaled down on a small head unit and does not
         * cost a large allocation on every state change.
         */
        const val ORB_PX = 320

        /**
         * Longer than the phone's, because a driver takes longer to answer.
         *
         * Still bounded: an open microphone in a car with nobody talking is the
         * one thing this surface must not leave running.
         */
        const val CAR_INACTIVITY_MS = 15_000L
    }
}
