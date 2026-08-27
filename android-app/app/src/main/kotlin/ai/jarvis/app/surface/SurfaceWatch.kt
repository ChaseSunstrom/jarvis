package ai.jarvis.app.surface

import android.util.Log
import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

/**
 * What the house has put up on its surface (M83), as the phone sees it (M103).
 *
 * Same shape as [ai.jarvis.app.tasks.TaskWatch] and for the same reason: the
 * device channel subscribes to `jarvis_surface_changed` first and lists
 * `jarvis/surface/list` second, so a panel that goes up between the two is
 * not missed for the life of the connection. The console draws each kind as
 * an instrument; the phone draws each as ONE LINE in its own vocabulary
 * ([line]) — a phone is glanced at, not watched.
 *
 * The kinds are the server's `KINDS` in `integrations/surface`, pinned by
 * `android-app/tools/surface_on_the_phone_test.py`; an unknown kind is still
 * listed, by its title, so a house newer than the app shows something rather
 * than nothing.
 */
object SurfaceWatch {
    private const val TAG = "JarvisSurface"

    const val EVENT = "jarvis_surface_changed"
    const val TYPE_LIST = "jarvis/surface/list"
    const val TYPE_REMOVE = "jarvis/surface/remove"

    /** The server's surface kinds (`integrations/surface/__init__.py` KINDS). */
    val KINDS: Set<String> = setOf("entity", "camera", "readings", "sky", "moments", "note", "page", "chart", "task")

    data class Panel(
        val id: String,
        val kind: String,
        val title: String,
        val entity: String = "",
        val camera: String = "",
        val area: String = "",
        val note: String = "",
        val url: String = "",
        val text: String = "",
        val task: String = "",
        val placedAt: Double = 0.0,
    )

    private val lock = Any()
    private val panels = ArrayList<Panel>()
    private val listeners = CopyOnWriteArrayList<(List<Panel>) -> Unit>()

    fun panels(): List<Panel> = synchronized(lock) { panels.toList() }

    fun listArgs(): JSONObject = JSONObject()

    /** `{"panel": id}` — the server's name for it, the same the console sends. */
    fun removeArgs(panelId: String): JSONObject = JSONObject().put("panel", panelId)

    /** The whole set arrives on every change, as the console gets it. */
    fun onEvent(frame: JSONObject?): Boolean {
        if (frame == null) return false
        val event = frame.optJSONObject("event") ?: return false
        if (event.optString("event_type") != EVENT) return false
        replace(event.optJSONObject("data")?.optJSONArray("panels"))
        return true
    }

    fun onListing(result: JSONObject?) {
        replace(result?.optJSONArray("panels"))
    }

    private fun replace(array: org.json.JSONArray?) {
        if (array == null) {
            Log.d(TAG, "a surface frame carried no panels")
            return
        }
        val fresh = ArrayList<Panel>(array.length())
        for (index in 0 until array.length()) {
            panelOf(array.optJSONObject(index))?.let(fresh::add)
        }
        synchronized(lock) {
            panels.clear()
            panels.addAll(fresh)
        }
        publish()
    }

    fun panelOf(raw: JSONObject?): Panel? {
        if (raw == null) return null
        val id = raw.optString("id").orEmpty()
        val kind = raw.optString("kind").orEmpty()
        if (id.isEmpty() || kind.isEmpty()) return null
        return Panel(
            id = id,
            kind = kind,
            title = raw.optString("title").orEmpty(),
            entity = raw.optString("entity").orEmpty(),
            camera = raw.optString("camera").orEmpty(),
            area = raw.optString("area").orEmpty(),
            note = raw.optString("note").orEmpty(),
            url = raw.optString("url").orEmpty(),
            text = raw.optString("text").orEmpty(),
            task = raw.optString("task").orEmpty(),
            placedAt = raw.optDouble("placed_at", 0.0),
        )
    }

    /**
     * One line per panel, in the phone's words. What the console draws as an
     * instrument, the phone says: the entity and (when the state is known to
     * the caller) its state, the camera's name, the room whose readings are up,
     * the note's first line, the page's title, the sky, the moments, a job.
     */
    fun line(panel: Panel, stateOf: (String) -> String? = { null }): String {
        val name = panel.title.ifEmpty { panel.entity.ifEmpty { panel.kind } }
        return when (panel.kind) {
            "entity" -> {
                val state = stateOf(panel.entity)
                if (state.isNullOrEmpty()) name else "$name · $state"
            }
            "camera" -> "Camera · ${panel.title.ifEmpty { panel.camera }}"
            "readings" -> "Readings · ${panel.area.ifEmpty { "the house" }}"
            "sky" -> "The sky · ${panel.title.ifEmpty { "next pass" }}"
            "moments" -> "Moments · ${panel.title.ifEmpty { "what Jarvis said" }}"
            "note" -> "Note · ${panel.title.ifEmpty { panel.text.lineSequence().firstOrNull().orEmpty() }}"
            "page" -> "Page · ${panel.title.ifEmpty { panel.url }}"
            "chart" -> "Chart · $name"
            "task" -> "Working · ${panel.title.ifEmpty { "a job" }}"
            else -> name
        }
    }

    fun reset() {
        synchronized(lock) { panels.clear() }
        publish()
    }

    fun listen(listener: (List<Panel>) -> Unit): () -> Unit {
        listeners.add(listener)
        listener(panels())
        return { listeners.remove(listener) }
    }

    private fun publish() {
        val snapshot = panels()
        for (listener in listeners) {
            try {
                listener(snapshot)
            } catch (t: Throwable) {
                Log.w(TAG, "a surface listener failed", t)
            }
        }
    }
}
