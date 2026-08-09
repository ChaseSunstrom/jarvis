package ai.jarvis.app.automation.accessibility

import android.graphics.Rect
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicLong

/**
 * The Android half of screen reading: adapt `AccessibilityNodeInfo` to the pure
 * [ScreenNode] interface, run [ScreenReaderCore] over it, and serialise the
 * result.
 *
 * All the interesting decisions — what is pruned, where the caps bite, how
 * handles are minted — live in `ScreenModel.kt`, which has no Android imports
 * and is mirrored by `android-app/tools/screen_prune_test.py`. This file is a
 * type adapter and a JSON writer, and is kept that thin so the part worth
 * reviewing is the part that can be run.
 *
 * On node recycling: this module does not call `AccessibilityNodeInfo.recycle()`.
 * It is deprecated as of API 33 (nodes are pooled by the framework) and
 * recycling a node another reference still points at throws
 * `IllegalStateException` at some later, unrelated call site. Leaving them to
 * the collector is the sanctioned behaviour on the API levels this app targets.
 */
object ScreenReader {

    private const val TAG = "JarvisScreenReader"

    private val snapshotCounter = AtomicLong(0)

    /** `s0`, `s1`, … Monotonic for the life of the process. */
    fun nextSnapshotId(): String = "s" + snapshotCounter.getAndIncrement()

    /**
     * Read the active window.
     *
     * @param activity the activity/window class from the last screen-change
     *   event, recorded in the snapshot so a stale handle can be spotted.
     * @return null only when there is no active window at all.
     */
    fun read(
        root: AccessibilityNodeInfo?,
        activity: String? = null,
        limits: ScreenReaderLimits = ScreenReaderLimits.DEFAULT,
        includeInvisible: Boolean = false,
        snapshotId: String = nextSnapshotId()
    ): ScreenSnapshot? {
        if (root == null) return null
        return try {
            ScreenReaderCore.read(
                root = AccessibilityScreenNode(root),
                snapshotId = snapshotId,
                limits = limits,
                activity = activity,
                includeInvisible = includeInvisible
            )
        } catch (t: Throwable) {
            Log.w(TAG, "screen read failed", t)
            null
        }
    }

    // --- serialisation ------------------------------------------------------

    /**
     * Compact JSON for the model. Keys are short because every one of them is
     * repeated up to [ScreenReaderLimits.maxNodes] times; absent/false fields
     * are omitted entirely rather than written as null.
     *
     * The caller is responsible for marking the enclosing result untrusted —
     * see `JSONObject.markUntrusted()` and [UntrustedScreenContent].
     */
    fun toJson(snapshot: ScreenSnapshot): JSONObject {
        val nodes = JSONArray()
        for (n in snapshot.nodes) nodes.put(nodeJson(n))
        return JSONObject()
            .put("snapshot", snapshot.id)
            .put("package", snapshot.packageName ?: JSONObject.NULL)
            .put("activity", snapshot.activity ?: JSONObject.NULL)
            .put("node_count", snapshot.nodes.size)
            .put("visited", snapshot.visited)
            .put("truncated", snapshot.truncated)
            .apply { snapshot.screen?.let { put("screen", it.compact()) } }
            .put("nodes", nodes)
    }

    fun nodeJson(n: UiNode): JSONObject {
        val o = JSONObject()
            .put("id", n.handle)
            .put("bounds", n.bounds.compact())
        n.text?.let { o.put("text", it) }
        n.contentDescription?.let { o.put("desc", it) }
        n.hint?.let { o.put("hint", it) }
        n.className?.let { o.put("class", it) }
        n.viewId?.let { o.put("view_id", it) }
        n.parent?.let { o.put("parent", it) }
        if (n.clickable) o.put("clickable", true)
        if (n.longClickable) o.put("long_clickable", true)
        if (n.editable) o.put("editable", true)
        if (n.scrollable) o.put("scrollable", true)
        if (n.checkable) o.put("checkable", true).put("checked", n.checked)
        if (!n.enabled) o.put("enabled", false)
        if (n.focused) o.put("focused", true)
        // Present so the model knows not to ask for the contents; the contents
        // are not here and never will be.
        if (n.password) o.put("password", true)
        if (n.collapsed) o.put("collapsed", true)
        return o
    }

    // --- resolving handles back to live nodes -------------------------------

    /**
     * Walk [path] down from [root].
     *
     * Returns null when the tree has changed shape, which is the common case a
     * second after a snapshot: the app redrew, a list scrolled, a dialog
     * appeared. A null here becomes a "stale handle, re-read the screen" error,
     * never a tap on whatever is now at that position.
     */
    fun resolvePath(root: AccessibilityNodeInfo?, path: List<Int>): AccessibilityNodeInfo? {
        var current = root ?: return null
        for (index in path) {
            if (index < 0 || index >= current.childCount) return null
            current = current.getChild(index) ?: return null
        }
        return current
    }

    /**
     * Cheap identity check for a resolved node: same class, same view id, same
     * label. Not cryptographic and not meant to be — it exists so that a handle
     * pointing at "the Send button" cannot silently become "the Delete button"
     * when a list re-flows between the read and the tap.
     */
    fun signatureOf(node: AccessibilityNodeInfo): String = ScreenReaderCore.signatureOf(
        ScreenReaderCore.shortClass(node.className?.toString()),
        ScreenReaderCore.shortViewId(node.viewIdResourceName),
        if (node.isPassword) null else ScreenReaderCore.clean(node.text?.toString(), 200),
        ScreenReaderCore.clean(node.contentDescription?.toString(), 200)
    )

    /** Screen rectangle of a live node. */
    fun boundsOf(node: AccessibilityNodeInfo): Bounds {
        val r = Rect()
        node.getBoundsInScreen(r)
        return Bounds(r.left, r.top, r.right, r.bottom)
    }

    /**
     * First ancestor (including [node]) that satisfies [predicate], up to
     * [maxHops]. Used to turn "the TextView you matched by text" into "the
     * clickable row that contains it", which is what the user meant.
     */
    fun climb(
        node: AccessibilityNodeInfo,
        maxHops: Int = 6,
        predicate: (AccessibilityNodeInfo) -> Boolean
    ): AccessibilityNodeInfo? {
        var current: AccessibilityNodeInfo? = node
        var hops = 0
        while (current != null && hops <= maxHops) {
            if (predicate(current)) return current
            current = try {
                current.parent
            } catch (t: Throwable) {
                null
            }
            hops++
        }
        return null
    }

    /**
     * First descendant (including [node]) that satisfies [predicate]. Bounded in
     * breadth and depth so a hostile tree cannot stall the caller.
     */
    fun descend(
        node: AccessibilityNodeInfo,
        maxDepth: Int = 8,
        maxVisits: Int = 400,
        predicate: (AccessibilityNodeInfo) -> Boolean
    ): AccessibilityNodeInfo? {
        var budget = maxVisits

        fun visit(n: AccessibilityNodeInfo, depth: Int): AccessibilityNodeInfo? {
            if (budget <= 0 || depth > maxDepth) return null
            budget--
            if (predicate(n)) return n
            val count = minOf(n.childCount, 100)
            for (i in 0 until count) {
                val child = n.getChild(i) ?: continue
                visit(child, depth + 1)?.let { return it }
            }
            return null
        }

        return visit(node, 0)
    }

    /**
     * Every node whose text or content description matches [needle], newest
     * window first. Case-insensitive substring, matching the framework's own
     * `findAccessibilityNodeInfosByText` semantics.
     */
    fun findByText(root: AccessibilityNodeInfo?, needle: String): List<AccessibilityNodeInfo> {
        val r = root ?: return emptyList()
        val byFramework: List<AccessibilityNodeInfo> = try {
            r.findAccessibilityNodeInfosByText(needle) ?: emptyList()
        } catch (t: Throwable) {
            Log.w(TAG, "findAccessibilityNodeInfosByText failed", t)
            emptyList()
        }
        if (byFramework.isNotEmpty()) return byFramework
        // The framework search does not look at hint text, and misses some
        // custom views. Fall back to our own walk.
        return collect(r) { node ->
            sequenceOf(node.text, node.contentDescription, node.hintText)
                .any { it?.toString()?.contains(needle, ignoreCase = true) == true }
        }
    }

    /** Requires FLAG_REPORT_VIEW_IDS, which [JarvisAccessibilityService] sets. */
    fun findByViewId(root: AccessibilityNodeInfo?, viewId: String): List<AccessibilityNodeInfo> {
        val r = root ?: return emptyList()
        val exact: List<AccessibilityNodeInfo> = try {
            r.findAccessibilityNodeInfosByViewId(viewId) ?: emptyList()
        } catch (t: Throwable) {
            Log.w(TAG, "findAccessibilityNodeInfosByViewId failed", t)
            emptyList()
        }
        if (exact.isNotEmpty()) return exact
        // Callers routinely pass the short form ("send_button") because that is
        // what a snapshot showed them.
        if (viewId.contains('/') || viewId.contains(':')) return emptyList()
        return collect(r) { node ->
            ScreenReaderCore.shortViewId(node.viewIdResourceName) == viewId
        }
    }

    fun findByContentDescription(
        root: AccessibilityNodeInfo?,
        needle: String
    ): List<AccessibilityNodeInfo> {
        val r = root ?: return emptyList()
        return collect(r) { node ->
            node.contentDescription?.toString()?.contains(needle, ignoreCase = true) == true
        }
    }

    /** Bounded breadth-first collect. Never returns more than [limit] matches. */
    fun collect(
        root: AccessibilityNodeInfo,
        limit: Int = 25,
        maxVisits: Int = 3_000,
        predicate: (AccessibilityNodeInfo) -> Boolean
    ): List<AccessibilityNodeInfo> {
        val out = ArrayList<AccessibilityNodeInfo>(limit)
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visits = 0
        while (queue.isNotEmpty() && out.size < limit && visits < maxVisits) {
            val node = queue.removeFirst()
            visits++
            try {
                if (predicate(node)) out.add(node)
                val count = minOf(node.childCount, 200)
                for (i in 0 until count) node.getChild(i)?.let(queue::addLast)
            } catch (t: Throwable) {
                // A node can die mid-walk when the app redraws. Skip it.
                Log.v(TAG, "node vanished during walk", t)
            }
        }
        return out
    }
}

/**
 * `AccessibilityNodeInfo` seen through the pure [ScreenNode] lens.
 *
 * Every accessor is defensive: a node can be invalidated by the app redrawing
 * at any point during a walk, and the framework signals that by throwing from
 * an ordinary getter. A dead node reads as an empty, invisible one, which the
 * pruner then drops.
 */
class AccessibilityScreenNode(private val node: AccessibilityNodeInfo) : ScreenNode {

    override val text: String? get() = safe { node.text?.toString() }
    override val contentDescription: String? get() = safe { node.contentDescription?.toString() }
    override val hint: String? get() = safe { node.hintText?.toString() }
    override val className: String? get() = safe { node.className?.toString() }
    override val viewId: String? get() = safe { node.viewIdResourceName }
    override val packageName: String? get() = safe { node.packageName?.toString() }

    override val bounds: Bounds
        get() = safe {
            val r = Rect()
            node.getBoundsInScreen(r)
            Bounds(r.left, r.top, r.right, r.bottom)
        } ?: Bounds.EMPTY

    override val isVisible: Boolean get() = safe { node.isVisibleToUser } ?: false
    override val isClickable: Boolean get() = safe { node.isClickable } ?: false
    override val isLongClickable: Boolean get() = safe { node.isLongClickable } ?: false
    override val isEditable: Boolean get() = safe { node.isEditable } ?: false
    override val isScrollable: Boolean get() = safe { node.isScrollable } ?: false
    override val isCheckable: Boolean get() = safe { node.isCheckable } ?: false
    override val isChecked: Boolean get() = safe { node.isChecked } ?: false
    override val isEnabled: Boolean get() = safe { node.isEnabled } ?: false
    override val isFocused: Boolean get() = safe { node.isFocused } ?: false
    override val isPassword: Boolean get() = safe { node.isPassword } ?: false
    override val childCount: Int get() = safe { node.childCount } ?: 0

    override fun childAt(index: Int): ScreenNode? =
        safe { node.getChild(index) }?.let { AccessibilityScreenNode(it) }

    private inline fun <T> safe(block: () -> T): T? = try {
        block()
    } catch (t: Throwable) {
        null
    }
}
