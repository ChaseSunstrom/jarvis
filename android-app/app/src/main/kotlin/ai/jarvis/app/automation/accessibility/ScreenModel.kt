package ai.jarvis.app.automation.accessibility

/**
 * PURE LOGIC — no Android imports, no org.json, no I/O, no clock.
 *
 * The screen-reading half of UI automation: an abstraction over one node of a
 * view hierarchy ([ScreenNode]), the compact description we hand to the model
 * ([UiNode] / [ScreenSnapshot]), and the walk that turns the first into the
 * second ([ScreenReaderCore]).
 *
 * It is pure so that the interesting part — what gets pruned, what gets a
 * handle, where the caps bite — can be fed a fake tree and asserted on without
 * a device. `android-app/tools/screen_prune_test.py` is that executable spec
 * and mirrors this file line for line; change one, change the other and re-run:
 *
 *     python3 android-app/tools/screen_prune_test.py
 */

/** Screen rectangle in device pixels, top-left origin. */
data class Bounds(val left: Int, val top: Int, val right: Int, val bottom: Int) {
    val width: Int get() = right - left
    val height: Int get() = bottom - top

    /** A zero-area node is laid out but draws nothing, so it is never useful. */
    val isEmpty: Boolean get() = width <= 0 || height <= 0

    val centerX: Int get() = left + width / 2
    val centerY: Int get() = top + height / 2
    val area: Long get() = width.toLong() * height.toLong()

    /** `l,t,r,b` — four numbers, because the model does not need prose. */
    fun compact(): String = "$left,$top,$right,$bottom"

    companion object {
        val EMPTY = Bounds(0, 0, 0, 0)
    }
}

/**
 * The slice of `AccessibilityNodeInfo` this module actually reads.
 *
 * Kept as an interface so [ScreenReaderCore] never touches Android: the real
 * implementation is `AccessibilityScreenNode` in `ScreenReader.kt`, and the
 * tests build trees of plain data.
 */
interface ScreenNode {
    val text: String?
    val contentDescription: String?

    /** `hintText` on API 26+; the placeholder of an empty text field. */
    val hint: String?

    /** Fully-qualified, e.g. `android.widget.Button`. Shortened on emit. */
    val className: String?

    /** Full resource id, e.g. `com.app:id/send`. Shortened on emit. */
    val viewId: String?
    val packageName: String?
    val bounds: Bounds

    /** `isVisibleToUser`. False for off-screen recycler rows and hidden views. */
    val isVisible: Boolean
    val isClickable: Boolean
    val isLongClickable: Boolean
    val isEditable: Boolean
    val isScrollable: Boolean
    val isCheckable: Boolean
    val isChecked: Boolean
    val isEnabled: Boolean
    val isFocused: Boolean

    /** True for password fields. Their text is NEVER emitted. */
    val isPassword: Boolean

    val childCount: Int

    /** Null for a missing/dead child, which the walk skips. */
    fun childAt(index: Int): ScreenNode?
}

/**
 * One node in the description handed to the model.
 *
 * [handle] is the ONLY thing an act-call should reference. Coordinates are
 * included for context and for gesture fallbacks, but the model is never asked
 * to aim: it says `ui_click {"handle":"n12"}` and the device resolves n12 back
 * to a live node, re-checking [signature] first.
 */
data class UiNode(
    /** Short stable id for this snapshot: `n0`, `n1`, … in emission order. */
    val handle: String,
    val text: String?,
    val contentDescription: String?,
    val hint: String?,
    /** Last dotted segment of the class name, e.g. `Button`. */
    val className: String?,
    /** Everything after `id/`, e.g. `send_button`. */
    val viewId: String?,
    val packageName: String?,
    val bounds: Bounds,
    val clickable: Boolean,
    val longClickable: Boolean,
    val editable: Boolean,
    val scrollable: Boolean,
    val checkable: Boolean,
    val checked: Boolean,
    val enabled: Boolean,
    val focused: Boolean,
    /** True when this is a password field. [text] is null in that case. */
    val password: Boolean,
    val depth: Int,
    /** Handle of the nearest emitted ancestor, for reconstructing structure. */
    val parent: String?,
    /** Child indices from the root. How a handle is resolved back to a node. */
    val path: List<Int>,
    /** Cheap identity check, re-verified before a handle is acted on. */
    val signature: String,
    /** True when [text] was gathered from non-interactive descendants. */
    val collapsed: Boolean = false
)

/** One read of one window. */
data class ScreenSnapshot(
    /** `s0`, `s1`, … Handles are only valid within the snapshot that minted them. */
    val id: String,
    val packageName: String?,
    val activity: String?,
    val nodes: List<UiNode>,
    /** True when a cap (nodes, chars, depth, visits) cut the walk short. */
    val truncated: Boolean,
    /** How many tree nodes were looked at, emitted or not. */
    val visited: Int,
    val screen: Bounds?
) {
    fun node(handle: String): UiNode? = nodes.firstOrNull { it.handle == handle }

    /**
     * Flattened human/model-readable text. This is DATA: it must be fenced with
     * [UntrustedScreenContent.fence] before it goes anywhere near a prompt.
     */
    fun flatText(): String = buildString {
        for (n in nodes) {
            val label = n.text ?: n.contentDescription ?: n.hint ?: continue
            if (label.isBlank()) continue
            append(n.handle).append(": ").append(label)
            val marks = buildList {
                if (n.clickable) add("clickable")
                if (n.editable) add("editable")
                if (n.scrollable) add("scrollable")
                if (n.checkable) add(if (n.checked) "checked" else "unchecked")
                if (!n.enabled) add("disabled")
            }
            if (marks.isNotEmpty()) marks.joinTo(this, ", ", " [", "]")
            append('\n')
        }
    }.trimEnd()
}

/**
 * Every cap in one place, so a review can see the whole budget at once.
 *
 * The caps exist for two reasons and both matter: a 3000-node Compose tree
 * would blow the model's context window, and an app can make its tree as deep
 * and as wide as it likes, so an unbounded walk is a denial-of-service on the
 * accessibility thread.
 */
data class ScreenReaderLimits(
    /** Hard cap on emitted nodes. */
    val maxNodes: Int = 200,
    /** Hard cap on tree depth. Real hierarchies are ~20 deep. */
    val maxDepth: Int = 40,
    /** Hard cap on emitted characters across all nodes. */
    val maxChars: Int = 12_000,
    /** Per-field text cap. Longer strings are cut with an ellipsis. */
    val maxTextChars: Int = 200,
    /** Hard cap on nodes LOOKED AT, emitted or not. Bounds the walk itself. */
    val maxVisited: Int = 4_000,
    /** Hard cap on children examined per node, for pathological lists. */
    val maxChildrenPerNode: Int = 300
) {
    companion object {
        val DEFAULT = ScreenReaderLimits()

        /** Tighter budget for `ui_wait_for`, which polls several times a second. */
        val POLLING = ScreenReaderLimits(maxNodes = 60, maxChars = 3_000, maxVisited = 1_500)
    }
}

/**
 * The tree walk: prune, cap, assign handles.
 *
 * Rules, in the order they apply to a node:
 *
 *  1. A null child is skipped. A dead subtree costs nothing.
 *  2. Past [ScreenReaderLimits.maxDepth] or [ScreenReaderLimits.maxVisited] the
 *     walk stops and the snapshot is marked `truncated`.
 *  3. A node is RENDERABLE when it is visible and has a non-empty rectangle.
 *     Non-renderable nodes are never emitted — but the walk still descends into
 *     them, because a container can report itself invisible while its children
 *     are on screen, and silently losing half the UI is worse than a few wasted
 *     visits. "Prune" here means "never appears in the output".
 *  4. A node is MEANINGFUL when it carries text, a content description or a
 *     hint, or when it is interactive (clickable, long-clickable, editable,
 *     scrollable or checkable). Pure layout containers are dropped; their
 *     children are still walked, so structure is flattened rather than lost.
 *  5. A clickable node with no label of its own COLLAPSES the text of its
 *     non-interactive descendants into itself, and those descendants are then
 *     not emitted. This is what turns a six-node button into one line.
 *  6. Password fields never emit their text. Not truncated — omitted.
 *  7. Handles are `n0`, `n1`, … in emission order, so the same tree always
 *     produces the same handles, and every handle in a snapshot is unique.
 */
object ScreenReaderCore {

    /** Appended when a field is cut at [ScreenReaderLimits.maxTextChars]. */
    const val ELLIPSIS = "…"

    fun read(
        root: ScreenNode?,
        snapshotId: String,
        limits: ScreenReaderLimits = ScreenReaderLimits.DEFAULT,
        activity: String? = null,
        includeInvisible: Boolean = false
    ): ScreenSnapshot {
        val state = WalkState(limits, includeInvisible)
        if (root != null) {
            state.walk(root, depth = 0, path = emptyList(), parent = null, insideCollapsed = false)
        }
        return ScreenSnapshot(
            id = snapshotId,
            packageName = root?.packageName,
            activity = activity,
            nodes = state.emitted,
            truncated = state.truncated,
            visited = state.visited,
            screen = root?.bounds
        )
    }

    /** Interactive in a way that matters for tiering and for collapsing. */
    fun isInteractive(node: ScreenNode): Boolean =
        node.isClickable || node.isLongClickable || node.isEditable ||
            node.isScrollable || node.isCheckable

    /** Carries something a human could read. */
    fun hasLabel(node: ScreenNode): Boolean =
        !node.text.isNullOrBlank() || !node.contentDescription.isNullOrBlank() ||
            !node.hint.isNullOrBlank()

    /** Rule 4. */
    fun isMeaningful(node: ScreenNode): Boolean = isInteractive(node) || hasLabel(node)

    /** Rule 3. */
    fun isRenderable(node: ScreenNode, includeInvisible: Boolean): Boolean =
        includeInvisible || (node.isVisible && !node.bounds.isEmpty)

    /** `android.widget.Button` -> `Button`. Null and blanks stay null. */
    fun shortClass(className: String?): String? =
        className?.trim()?.takeIf { it.isNotEmpty() }?.substringAfterLast('.')?.takeIf { it.isNotEmpty() }

    /** `com.app:id/send_button` -> `send_button`. */
    fun shortViewId(viewId: String?): String? =
        viewId?.trim()?.takeIf { it.isNotEmpty() }?.substringAfterLast('/')?.takeIf { it.isNotEmpty() }

    /** Whitespace-collapsed, trimmed, capped. Blank becomes null. */
    fun clean(raw: String?, max: Int): String? {
        if (raw == null) return null
        val squashed = WHITESPACE.replace(raw, " ").trim()
        if (squashed.isEmpty()) return null
        return if (squashed.length <= max) squashed else squashed.take(max) + ELLIPSIS
    }

    /**
     * Identity of a node, cheap to recompute. Re-checked before a handle is
     * acted on, so a stale handle fails loudly instead of tapping whatever now
     * occupies that position in the tree.
     */
    fun signatureOf(
        className: String?,
        viewId: String?,
        text: String?,
        contentDescription: String?
    ): String = listOf(
        className.orEmpty(),
        viewId.orEmpty(),
        text.orEmpty().take(SIGNATURE_TEXT),
        contentDescription.orEmpty().take(SIGNATURE_TEXT)
    ).joinToString("|")

    private const val SIGNATURE_TEXT = 40
    private val WHITESPACE = Regex("\\s+")
}

/** Bytes a node costs beyond its strings: handle, bounds, flags. */
private const val PER_NODE_OVERHEAD = 40
private const val COLLAPSE_VISITS = 40
private const val COLLAPSE_DEPTH = 6
private const val COLLAPSE_BREADTH = 20

/**
 * Mutable bookkeeping for one [ScreenReaderCore.read].
 *
 * A file-private top-level class rather than a nested one so that every call
 * back into [ScreenReaderCore] is written out in full — the pruning rules are
 * the part of this module worth reading, and they should be greppable.
 */
private class WalkState(
    val limits: ScreenReaderLimits,
    val includeInvisible: Boolean
) {
    val emitted = ArrayList<UiNode>()
    var truncated = false
    var visited = 0
    var chars = 0

    fun walk(
        node: ScreenNode,
        depth: Int,
        path: List<Int>,
        parent: String?,
        insideCollapsed: Boolean
    ) {
        if (visited >= limits.maxVisited) {
            truncated = true
            return
        }
        visited++
        if (depth > limits.maxDepth) {
            truncated = true
            return
        }

        val renderable = ScreenReaderCore.isRenderable(node, includeInvisible)
        val interactive = ScreenReaderCore.isInteractive(node)

        // Rule 5: a text-only node swallowed by a collapsed clickable ancestor
        // is not emitted again. Interactive descendants still are.
        val absorbed = insideCollapsed && !interactive

        var handle: String? = null
        var collapsedHere = false

        if (renderable && ScreenReaderCore.isMeaningful(node) && !absorbed) {
            val emit = emitNode(node, depth, path, parent)
            if (emit == null) {
                truncated = true
            } else {
                handle = emit.first.handle
                collapsedHere = emit.second
            }
        }

        val childBudget = minOf(node.childCount, limits.maxChildrenPerNode)
        if (childBudget < node.childCount) truncated = true
        for (i in 0 until childBudget) {
            val child = node.childAt(i) ?: continue
            walk(
                node = child,
                depth = depth + 1,
                path = path + i,
                parent = handle ?: parent,
                insideCollapsed = collapsedHere || insideCollapsed
            )
        }
    }

    /** Null when a cap stopped us. Second element: did we collapse text in? */
    fun emitNode(
        node: ScreenNode,
        depth: Int,
        path: List<Int>,
        parent: String?
    ): Pair<UiNode, Boolean>? {
        if (emitted.size >= limits.maxNodes) return null
        if (chars >= limits.maxChars) return null

        val password = node.isPassword
        // Rule 6. A password field's contents are never described, at any
        // verbosity, to anyone. The field itself still shows up so it can be
        // targeted.
        val ownText =
            if (password) null else ScreenReaderCore.clean(node.text, limits.maxTextChars)
        val desc = ScreenReaderCore.clean(node.contentDescription, limits.maxTextChars)
        val hint = ScreenReaderCore.clean(node.hint, limits.maxTextChars)

        var collapsed = false
        var text = ownText
        if (text == null && desc == null && (node.isClickable || node.isLongClickable)) {
            val gathered = collapseText(node, limits.maxTextChars)
            if (gathered != null) {
                text = gathered
                collapsed = true
            }
        }

        val shortCls = ScreenReaderCore.shortClass(node.className)
        val shortId = ScreenReaderCore.shortViewId(node.viewId)
        val handle = "n" + emitted.size

        chars += (text?.length ?: 0) + (desc?.length ?: 0) + (hint?.length ?: 0) +
            (shortCls?.length ?: 0) + (shortId?.length ?: 0) + PER_NODE_OVERHEAD

        val ui = UiNode(
            handle = handle,
            text = text,
            contentDescription = desc,
            hint = hint,
            className = shortCls,
            viewId = shortId,
            packageName = node.packageName,
            bounds = node.bounds,
            clickable = node.isClickable,
            longClickable = node.isLongClickable,
            editable = node.isEditable,
            scrollable = node.isScrollable,
            checkable = node.isCheckable,
            checked = node.isChecked,
            enabled = node.isEnabled,
            focused = node.isFocused,
            password = password,
            depth = depth,
            parent = parent,
            path = path,
            signature = ScreenReaderCore.signatureOf(shortCls, shortId, text, desc),
            collapsed = collapsed
        )
        emitted.add(ui)
        return ui to collapsed
    }

    /**
     * Gather the labels of non-interactive descendants of a clickable node.
     * Bounded in both breadth and depth: this runs inside the main walk and must
     * not turn it quadratic.
     */
    fun collapseText(node: ScreenNode, max: Int): String? {
        val parts = ArrayList<String>(4)
        var budget = COLLAPSE_VISITS

        fun gather(n: ScreenNode, depth: Int) {
            if (budget <= 0 || depth > COLLAPSE_DEPTH) return
            budget--
            if (!ScreenReaderCore.isRenderable(n, includeInvisible)) return
            if (n.isPassword) return
            if (depth > 0 && !ScreenReaderCore.isInteractive(n)) {
                val label = ScreenReaderCore.clean(n.text, max)
                    ?: ScreenReaderCore.clean(n.contentDescription, max)
                if (label != null) parts.add(label)
            }
            // Do not dive under a nested interactive node: its own label belongs
            // to it, not to us.
            if (depth > 0 && ScreenReaderCore.isInteractive(n)) return
            val children = minOf(n.childCount, COLLAPSE_BREADTH)
            for (i in 0 until children) {
                val c = n.childAt(i) ?: continue
                gather(c, depth + 1)
            }
        }

        gather(node, 0)
        if (parts.isEmpty()) return null
        val joined = parts.joinToString(" ")
        return if (joined.length <= max) joined else joined.take(max) + ScreenReaderCore.ELLIPSIS
    }
}
