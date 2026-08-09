package ai.jarvis.app.support

import android.app.Activity
import android.view.View
import android.view.ViewGroup
import androidx.test.espresso.ViewInteraction
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.matcher.ViewMatchers.withClassName
import org.hamcrest.Matchers.equalTo
import java.util.regex.Pattern

/**
 * Finding things in a UI that has no resource ids.
 *
 * Every Jarvis screen is built programmatically (see `ui/JarvisUi.kt` for why),
 * so there is no `R.id.talk_button` to match on. The two stable handles that are
 * left are the view's CLASS and its TEXT, and both are used here rather than
 * position — an index into a `LinearLayout` is a matcher that silently starts
 * pointing at a different view the day somebody adds a spacer.
 */
object Views {

    /** Espresso interaction for the one view of [type] on screen. */
    fun ofType(type: Class<out View>): ViewInteraction =
        onView(withClassName(equalTo(type.name)))

    /**
     * Depth-first search of an activity's whole decor view for the first view of
     * [type].
     *
     * For the cases Espresso cannot answer: reaching an actual object to call a
     * method on it (`JarvisOrbView`), or asserting a property that is not a
     * matcher. MUST be called on the main thread — use inside
     * `ActivityScenario.onActivity {}` or `runOnUiThread`.
     */
    fun <T : View> firstOfType(activity: Activity, type: Class<T>): T? =
        firstOfType(activity.window?.decorView ?: return null, type)

    fun <T : View> firstOfType(root: View, type: Class<T>): T? {
        if (type.isInstance(root)) {
            @Suppress("UNCHECKED_CAST")
            return root as T
        }
        if (root !is ViewGroup) return null
        for (i in 0 until root.childCount) {
            firstOfType(root.getChildAt(i), type)?.let { return it }
        }
        return null
    }

    /** Every view of [type] under [root], depth first. Main thread only. */
    fun <T : View> allOfType(root: View, type: Class<T>): List<T> {
        val out = mutableListOf<T>()
        collect(root, type, out)
        return out
    }

    private fun <T : View> collect(root: View, type: Class<T>, into: MutableList<T>) {
        if (type.isInstance(root)) {
            @Suppress("UNCHECKED_CAST")
            into.add(root as T)
        }
        if (root is ViewGroup) {
            for (i in 0 until root.childCount) collect(root.getChildAt(i), type, into)
        }
    }

    /**
     * A case-insensitive UiAutomator text pattern.
     *
     * Load-bearing, not tidiness. `JarvisUi.pill` and `JarvisUi.ghost` set
     * `isAllCaps = true`, which is a *display* transformation: the button reads
     * "YES" on screen while `TextView.getText()` — and therefore the
     * accessibility node UiAutomator matches against — still holds the original
     * "yes". A companion question's option buttons are labelled with the
     * server's own option strings, so matching them case-sensitively either way
     * round is a coin flip.
     */
    fun textIgnoringCase(text: String): Pattern =
        Pattern.compile(Pattern.quote(text), Pattern.CASE_INSENSITIVE)

    /** As [textIgnoringCase], but matching anywhere in the node's text. */
    fun containingIgnoringCase(text: String): Pattern =
        Pattern.compile(".*" + Pattern.quote(text) + ".*", Pattern.CASE_INSENSITIVE or Pattern.DOTALL)
}
