package io.homeassistant.companion.android.jarvis

import android.app.Activity
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.util.TypedValue
import android.view.Gravity
import android.widget.TextView

/** Shared look-and-feel helpers for the Jarvis surfaces. */
object JarvisUi {
    const val ACCENT = 0xFF3FD8FF.toInt()
    const val DIM = 0xCC7FD7EA.toInt()
    const val BG = 0xFF04070C.toInt()

    fun dp(context: Context, v: Int): Int =
        (v * context.resources.displayMetrics.density).toInt()

    /** Edge-to-edge, system bars hidden (swipe to reveal). */
    fun immersive(activity: Activity) {
        val w = activity.window
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            w.setDecorFitsSystemWindows(false)
            w.insetsController?.let { c ->
                c.hide(android.view.WindowInsets.Type.systemBars())
                c.systemBarsBehavior =
                    android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            @Suppress("DEPRECATION")
            w.decorView.systemUiVisibility = (
                android.view.View.SYSTEM_UI_FLAG_FULLSCREEN
                    or android.view.View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    or android.view.View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    or android.view.View.SYSTEM_UI_FLAG_LAYOUT_STABLE)
        }
    }

    fun transcriptView(context: Context): TextView = TextView(context).apply {
        setTextColor(DIM)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
        gravity = Gravity.CENTER
        typeface = Typeface.MONOSPACE
    }

    fun responseView(context: Context): TextView = TextView(context).apply {
        setTextColor(Color.WHITE)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
        gravity = Gravity.CENTER
        setPadding(0, dp(context, 10), 0, 0)
    }
}
