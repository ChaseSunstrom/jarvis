package io.homeassistant.companion.android.jarvis

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

/**
 * Minimal, dependency-free settings screen for the self-contained assist
 * client: Home Assistant base URL, a long-lived access token, and the pipeline
 * name. Built programmatically to keep the jarvis flavor self-contained (no
 * layout XML / resource churn against the fork).
 */
class JarvisSettingsActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val cfg = JarvisConfig(this)

        val pad = (16 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(0xFF04070C.toInt())
            setPadding(pad, pad, pad, pad)
        }

        fun label(text: String) = TextView(this).apply {
            this.text = text
            setTextColor(0xFF3FD8FF.toInt())
            textSize = 13f
            setPadding(0, pad, 0, pad / 3)
        }

        fun field(hint: String, value: String, password: Boolean = false) = EditText(this).apply {
            this.hint = hint
            setText(value)
            setTextColor(Color.WHITE)
            setHintTextColor(0xFF5A7A86.toInt())
            inputType = if (password) {
                InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            } else {
                InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            }
            setSingleLine(!password)
        }

        val title = TextView(this).apply {
            text = "JARVIS"
            setTextColor(0xFF3FD8FF.toInt())
            textSize = 26f
            letterSpacing = 0.4f
            gravity = Gravity.CENTER
            setPadding(0, pad, 0, pad)
        }

        val urlField = field("http://homeassistant.local:8123", cfg.haUrl)
        val tokenField = field("long-lived access token", cfg.token, password = true)
        val pipelineField = field("Jarvis", cfg.pipeline)

        val save = Button(this).apply {
            text = "SAVE"
            setOnClickListener {
                val url = urlField.text.toString().trim()
                val token = tokenField.text.toString().trim()
                if (url.isEmpty() || token.isEmpty()) {
                    Toast.makeText(this@JarvisSettingsActivity,
                        "URL and token are required", Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }
                cfg.haUrl = url
                cfg.token = token
                cfg.pipeline = pipelineField.text.toString().trim()
                Toast.makeText(this@JarvisSettingsActivity, "Saved", Toast.LENGTH_SHORT).show()
                finish()
            }
        }

        val hint = TextView(this).apply {
            text = "Create a long-lived token in Home Assistant: profile → Security → " +
                "Long-lived access tokens. Use the URL you reach HA on " +
                "(WireGuard/LAN)."
            setTextColor(0xFF5A7A86.toInt())
            textSize = 12f
            setPadding(0, pad, 0, 0)
        }

        val lp = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
        root.addView(title, lp)
        root.addView(label("Home Assistant URL"), lp)
        root.addView(urlField, lp)
        root.addView(label("Access token"), lp)
        root.addView(tokenField, lp)
        root.addView(label("Pipeline name"), lp)
        root.addView(pipelineField, lp)
        root.addView(save, lp)
        root.addView(hint, lp)
        setContentView(root)
    }
}
