package ai.jarvis.app.support

import android.app.Activity
import android.view.View
import android.view.ViewGroup
import androidx.test.espresso.ViewInteraction
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.matcher.ViewMatchers.withClassName
import androidx.test.uiautomator.By
import androidx.test.uiautomator.BySelector
import androidx.test.uiautomator.Direction
import androidx.test.uiautomator.StaleObjectException
import androidx.test.uiautomator.UiObject2
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
    fun <T : View> firstOfType(activity: Activity, type: Class<T>): T? {
        val decor = activity.window?.decorView ?: return null
        return firstOfType(decor, type)
    }

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

    /**
     * Find a node, scrolling the screen's scrollable container to reach it.
     *
     * Every Jarvis screen with more than a few controls puts them in a
     * `ScrollView`, and how much of one fits depends entirely on the emulator
     * profile: a consent prompt that fits on a 1080×1920 Pixel image needs
     * scrolling on a smaller or denser one. A test that assumed everything is on
     * screen at once would pass on one CI image and fail on the next for no
     * reason anybody could act on.
     *
     * Scrolls to the TOP first, then works down. Without that, a screen already
     * scrolled by a previous assertion would be searched in one direction only,
     * and a control above the current position would read as absent.
     *
     * Returns the object with its bounds on screen, so a caller can click it —
     * `UiObject2.click` targets the node's visible centre, which for a node
     * scrolled off the screen is not where the node is.
     */
    fun findScrolling(selector: BySelector, maxScrolls: Int = DEFAULT_MAX_SCROLLS): UiObject2? {
        val device = Device.ui
        device.findObject(selector)?.let { return it }

        var steps = 0
        while (steps < maxScrolls && scrollOnce(Direction.UP, 1f)) steps++
        device.findObject(selector)?.let { return it }

        steps = 0
        while (steps < maxScrolls) {
            if (!scrollOnce(Direction.DOWN, SCROLL_STEP)) break
            device.findObject(selector)?.let { return it }
            steps++
        }
        return device.findObject(selector)
    }

    /**
     * The text of every CLICKABLE node on the screen, gathered while scrolling
     * the whole of it.
     *
     * The proof shape for "there is no control that does X" — `ConsentGateTest`
     * uses it to show a Tier-3 prompt offers no way to remember an answer.
     * Scrolling is not a nicety there: the consent prompt is a `ScrollView` that
     * overflows on the CI emulator profile, and a one-screen
     * `findObjects(By.clickable(true))` would report the absence of a control
     * that is merely below the fold. An assertion about absence has to have
     * looked everywhere before it means anything.
     *
     * Nodes with no text contribute an empty string rather than being dropped,
     * so a caller can tell "no clickable nodes at all" (empty list — usually a
     * sign the screen never rendered) from "clickable nodes, none labelled".
     */
    fun clickableTextsScrolling(maxScrolls: Int = DEFAULT_MAX_SCROLLS): List<String> {
        val out = LinkedHashSet<String>()
        fun collect() {
            val found = try {
                Device.ui.findObjects(By.clickable(true))
            } catch (e: StaleObjectException) {
                emptyList<UiObject2>()
            }
            for (node in found) {
                out += try {
                    node.text.orEmpty()
                } catch (e: StaleObjectException) {
                    continue
                }
            }
        }

        var steps = 0
        while (steps < maxScrolls && scrollOnce(Direction.UP, 1f)) steps++
        collect()
        steps = 0
        while (steps < maxScrolls) {
            if (!scrollOnce(Direction.DOWN, SCROLL_STEP)) break
            collect()
            steps++
        }
        return out.toList()
    }

    /**
     * One scroll of whatever scrollable container is on screen. False when there
     * is none, or it will not move any further.
     *
     * The container is re-found on every step on purpose: a `UiObject2` holds an
     * `AccessibilityNodeInfo` that the scroll itself can invalidate, and a
     * `StaleObjectException` halfway through a search is not a test failure —
     * it just means the tree changed, which is what scrolling does.
     */
    /**
     * Scroll the first scrollable that can actually move that way.
     *
     * This used to take `findObject(By.scrollable(true))` — THE first
     * scrollable node — and the settings screen now has two: the console's tab
     * strip, a `HorizontalScrollView` at the top, and the settings body under
     * it. The strip won, every vertical scroll went to a container that cannot
     * scroll vertically, the body never moved, and nine tests failed saying
     * "No button labelled APP INFO on screen" about a button that was on the
     * screen and simply below the fold.
     *
     * Trying each in turn rather than filtering by class name, because the
     * question being asked is "can anything here scroll this way", and
     * `UiObject2.scroll` already answers exactly that — it returns false when
     * the node cannot scroll further in the given direction. Filtering on
     * `HorizontalScrollView` would fix this screen and break on the next
     * container that happens to be scrollable.
     */
    private fun scrollOnce(direction: Direction, fraction: Float): Boolean {
        for (candidate in Device.ui.findObjects(By.scrollable(true)).orEmpty()) {
            try {
                if (candidate.scroll(direction, fraction)) return true
            } catch (e: StaleObjectException) {
                // The screen changed under us — whatever is there now is a
                // different question, and the caller re-queries anyway.
                return false
            }
        }
        return false
    }

    private const val DEFAULT_MAX_SCROLLS = 10
    private const val SCROLL_STEP = 0.5f
}
