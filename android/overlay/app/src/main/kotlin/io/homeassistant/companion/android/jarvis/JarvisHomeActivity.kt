package io.homeassistant.companion.android.jarvis

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import io.homeassistant.companion.android.jarvis.assist.JarvisConversation

/**
 * The Jarvis home / launcher screen — the face of the app. Opening the app
 * lands here (not Home Assistant's WebView): the cinematic orb, a tap-to-talk
 * control, and the live transcript/response, all driven by the shared
 * [JarvisConversation] engine. Home Assistant's own dashboard/onboarding is
 * still reachable behind the DASHBOARD button for when the full web UI is
 * wanted.
 */
class JarvisHomeActivity : Activity(), JarvisConversation.Ui {

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

    private fun buildUi(): ViewGroup {
        val root = FrameLayout(this).apply { setBackgroundColor(JarvisUi.BG) }

        orbView = JarvisOrbView(this).apply {
            chromeEnabled = true
            setStateLabel("TAP TO SPEAK")
            startEntrance()
        }
        root.addView(orbView, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            val m = JarvisUi.dp(this@JarvisHomeActivity, 24)
            setPadding(m, 0, m, JarvisUi.dp(this@JarvisHomeActivity, 40))
        }
        transcriptView = JarvisUi.transcriptView(this)
        responseView = JarvisUi.responseView(this)

        talkButton = pill("TAP TO SPEAK") { toggleTalk() }
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(0, JarvisUi.dp(this@JarvisHomeActivity, 18), 0, 0)
            addView(ghost("DASHBOARD") { openDashboard() })
            addView(spacer())
            addView(ghost("SETTINGS") {
                startActivity(Intent(this@JarvisHomeActivity, JarvisSettingsActivity::class.java))
            })
        }

        col.addView(transcriptView)
        col.addView(responseView)
        col.addView(talkButton, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply { topMargin = JarvisUi.dp(this@JarvisHomeActivity, 22) })
        col.addView(row)

        root.addView(col, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT,
            Gravity.BOTTOM))
        return root
    }

    private fun toggleTalk() {
        val c = convo
        if (c != null && c.isRunning) {
            c.stop(); showIdle(); return
        }
        if (!config.isConfigured) {
            startActivity(Intent(this, JarvisSettingsActivity::class.java)); return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC); return
        }
        transcriptView.text = ""; responseView.text = ""
        talkButton.text = "LISTENING… (TAP TO STOP)"
        convo = JarvisConversation(this, config, this, inactivityMs = 12000L).also { it.start() }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC &&
            grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED
        ) toggleTalk()
    }

    private fun openDashboard() {
        // Hand off to Home Assistant's own launch/onboarding/WebView flow.
        try {
            startActivity(Intent().setClassName(
                packageName, "io.homeassistant.companion.android.launch.LaunchActivity"))
        } catch (e: Exception) {
            Toast.makeText(this, "Home Assistant dashboard unavailable", Toast.LENGTH_SHORT).show()
        }
    }

    private fun showIdle() {
        orbView.setAmplitude(0f)
        orbView.setMode(JarvisOrbView.Mode.LISTENING)
        orbView.setStateLabel("TAP TO SPEAK")
        talkButton.text = "TAP TO SPEAK"
        if (!config.isConfigured) {
            responseView.text = "Tap SETTINGS to connect to Home Assistant."
        }
    }

    // --- JarvisConversation.Ui -------------------------------------------

    override fun onMode(mode: JarvisOrbView.Mode, label: String) {
        orbView.setMode(mode); orbView.setStateLabel(label)
        talkButton.text = when (label) {
            "LISTENING" -> "LISTENING… (TAP TO STOP)"
            else -> "$label… (TAP TO STOP)"
        }
    }
    override fun onAmplitude(level: Float) = orbView.setAmplitude(level)
    override fun onTranscript(text: String) { transcriptView.text = text }
    override fun onResponse(text: String) { responseView.text = text }
    override fun onError(message: String) {
        responseView.text = message; orbView.setStateLabel("ERROR")
    }
    override fun onIdle() { showIdle() }

    override fun onDestroy() {
        convo?.stop(); convo = null
        super.onDestroy()
    }

    // --- tiny programmatic widgets ---------------------------------------

    private fun pill(label: String, onClick: () -> Unit): Button = Button(this).apply {
        text = label
        isAllCaps = true
        setTextColor(JarvisUi.ACCENT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
        letterSpacing = 0.15f
        background = GradientDrawable().apply {
            cornerRadius = JarvisUi.dp(this@JarvisHomeActivity, 26).toFloat()
            setColor(0x2233D8FF)
            setStroke(JarvisUi.dp(this@JarvisHomeActivity, 1), JarvisUi.ACCENT)
        }
        setPadding(
            JarvisUi.dp(this@JarvisHomeActivity, 34), JarvisUi.dp(this@JarvisHomeActivity, 16),
            JarvisUi.dp(this@JarvisHomeActivity, 34), JarvisUi.dp(this@JarvisHomeActivity, 16))
        setOnClickListener { onClick() }
    }

    private fun ghost(label: String, onClick: () -> Unit): Button = Button(this).apply {
        text = label
        isAllCaps = true
        setTextColor(JarvisUi.DIM)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        letterSpacing = 0.12f
        background = GradientDrawable().apply {
            cornerRadius = JarvisUi.dp(this@JarvisHomeActivity, 22).toFloat()
            setColor(Color.TRANSPARENT)
            setStroke(JarvisUi.dp(this@JarvisHomeActivity, 1), 0x5533D8FF)
        }
        setPadding(
            JarvisUi.dp(this@JarvisHomeActivity, 22), JarvisUi.dp(this@JarvisHomeActivity, 12),
            JarvisUi.dp(this@JarvisHomeActivity, 22), JarvisUi.dp(this@JarvisHomeActivity, 12))
        setOnClickListener { onClick() }
    }

    private fun spacer(): android.view.View = android.view.View(this).apply {
        layoutParams = LinearLayout.LayoutParams(JarvisUi.dp(this@JarvisHomeActivity, 14), 1)
    }

    companion object {
        private const val REQ_MIC = 4712
    }
}
