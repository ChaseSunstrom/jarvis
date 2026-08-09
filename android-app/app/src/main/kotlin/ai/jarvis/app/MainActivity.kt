package ai.jarvis.app

import ai.jarvis.app.assist.JarvisConversation
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.JarvisScreens
import ai.jarvis.app.ui.JarvisUi
import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * The Jarvis home — the face of the app. Opening it lands on the orb, not a
 * dashboard: tap to talk, watch the transcript and the reply, and get to the
 * three other surfaces from the bottom row.
 *
 * The conversation itself is [JarvisConversation], the same engine the assist
 * popup uses, so the two behave identically down to the barge-in timing.
 */
class MainActivity : Activity(), JarvisConversation.Ui {

    private lateinit var orbView: JarvisOrbView
    private lateinit var transcriptView: TextView
    private lateinit var responseView: TextView
    private lateinit var talkButton: Button
    private lateinit var config: JarvisConfig
    private var convo: JarvisConversation? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        JarvisUi.immersive(this)
        config = JarvisConfig(this)
        setContentView(buildUi())
        showIdle()
    }

    override fun onResume() {
        super.onResume()
        // Settings may have changed the server behind our back.
        if (convo?.isRunning != true) showIdle()
    }

    private fun buildUi(): ViewGroup {
        val root = FrameLayout(this).apply { setBackgroundColor(JarvisUi.BG) }

        orbView = JarvisOrbView(this).apply {
            chromeEnabled = true
            setStateLabel("TAP TO SPEAK")
            startEntrance()
        }
        root.addView(
            orbView,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )

        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            val m = JarvisUi.dp(this@MainActivity, 24)
            setPadding(m, 0, m, JarvisUi.dp(this@MainActivity, 36))
        }

        transcriptView = JarvisUi.transcriptView(this)
        responseView = JarvisUi.responseView(this)
        talkButton = JarvisUi.pill(this, "TAP TO SPEAK") { toggleTalk() }

        val nav = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(0, JarvisUi.dp(this@MainActivity, 18), 0, 0)
            addView(JarvisUi.ghost(this@MainActivity, "MANAGE") { openManagement() })
            addView(navSpacer())
            addView(JarvisUi.ghost(this@MainActivity, "AUTOMATIONS") { openAutomations() })
            addView(navSpacer())
            addView(JarvisUi.ghost(this@MainActivity, "SETTINGS") { openSettings() })
        }

        col.addView(transcriptView)
        col.addView(responseView)
        col.addView(
            talkButton,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = JarvisUi.dp(this@MainActivity, 22) }
        )
        col.addView(nav)

        root.addView(
            col,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM
            )
        )
        return root
    }

    private fun navSpacer(): android.view.View = android.view.View(this).apply {
        layoutParams = LinearLayout.LayoutParams(JarvisUi.dp(this@MainActivity, 10), 1)
    }

    // --- actions ------------------------------------------------------------

    private fun toggleTalk() {
        val c = convo
        if (c != null && c.isRunning) {
            c.stop(); showIdle(); return
        }
        if (!config.isConfigured) {
            openSettings(); return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC); return
        }
        transcriptView.text = ""
        responseView.text = ""
        talkButton.text = "LISTENING… (TAP TO STOP)"
        convo = JarvisConversation(this, config, this, inactivityMs = 12000L).also { it.start() }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC &&
            grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED
        ) toggleTalk()
    }

    private fun openSettings() =
        startActivity(Intent(this, SettingsActivity::class.java))

    private fun openManagement() {
        if (!config.isConfigured) {
            responseView.text = "Set the server URL and token in Settings first."
            return
        }
        startActivity(Intent(this, ManagementActivity::class.java))
    }

    private fun openAutomations() {
        JarvisScreens.open(this, JarvisScreens.AUTOMATIONS, "Automations")
    }

    private fun showIdle() {
        orbView.setAmplitude(0f)
        orbView.setMode(JarvisOrbView.Mode.LISTENING)
        orbView.setStateLabel("TAP TO SPEAK")
        talkButton.text = "TAP TO SPEAK"
        if (!config.isConfigured) {
            responseView.text = "Tap SETTINGS to point me at your Jarvis server."
        }
    }

    // --- JarvisConversation.Ui (main thread) --------------------------------

    override fun onMode(mode: JarvisOrbView.Mode, label: String) {
        orbView.setMode(mode)
        orbView.setStateLabel(label)
        talkButton.text = when (label) {
            "LISTENING" -> "LISTENING… (TAP TO STOP)"
            else -> "$label… (TAP TO STOP)"
        }
    }

    override fun onAmplitude(level: Float) = orbView.setAmplitude(level)

    override fun onTranscript(text: String) {
        transcriptView.text = text
    }

    override fun onResponse(text: String) {
        responseView.text = text
    }

    override fun onError(message: String) {
        responseView.text = message
        orbView.setStateLabel("ERROR")
    }

    override fun onIdle() = showIdle()

    override fun onDestroy() {
        convo?.stop()
        convo = null
        super.onDestroy()
    }

    companion object {
        private const val REQ_MIC = 4712
    }
}
