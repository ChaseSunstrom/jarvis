package ai.jarvis.app.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.util.Log
import android.widget.Toast

/**
 * Screens that live in another module, launched by name.
 *
 * The automation module owns `ai.jarvis.app.automation.**`, and this module
 * must not import from it — otherwise neither can be built or reviewed on its
 * own. Launching by class name keeps the dependency one-way and at runtime:
 * both activities are already declared in AndroidManifest.xml, so the
 * automation module only has to provide classes with these exact names.
 *
 * If a screen is missing the user gets a toast, not a crash.
 */
object JarvisScreens {

    /** The automations list / rule editor. */
    const val AUTOMATIONS = "ai.jarvis.app.automation.ui.AutomationsActivity"

    /** The user-viewable audit log of every executed action. */
    const val AUDIT_LOG = "ai.jarvis.app.automation.ui.AuditLogActivity"

    private const val TAG = "JarvisScreens"

    /**
     * Start [className] within this app. Returns false (and toasts) when the
     * automation module is not present in this build.
     */
    fun open(context: Context, className: String, label: String): Boolean {
        val intent = Intent().setClassName(context.packageName, className)
        return try {
            context.startActivity(intent)
            true
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "$className is not installed in this build", e)
            Toast.makeText(context, "$label is not available in this build", Toast.LENGTH_SHORT)
                .show()
            false
        }
    }
}
