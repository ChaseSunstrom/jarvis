package ai.jarvis.app.automation.tasks

import android.content.Context
import android.util.Log
import ai.jarvis.app.automation.policy.ActionTier
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Where tasks live: one JSON file in app-private storage,
 * `filesDir/jarvis/tasks.json`.
 *
 * A file rather than DataStore or a database, for the same reason the audit log
 * is a file: the user can read it, export it, and put it back, and a corrupt
 * write costs one file rather than a schema migration. Writes are serialised by
 * a mutex and go through a temp file plus rename, so a kill mid-write leaves
 * the previous version intact rather than a half-written one.
 *
 * ## The rule this class exists to enforce
 *
 * The server may push tasks. A pushed task that contains a CONFIRM-tier action
 * is stored **disabled** and stays that way until a human turns it on in the
 * app. [TaskSafety] decides; this class is the only thing that writes the
 * `enabled` field, and [setEnabledByUser] is the only path that can set
 * `enabledByUser`. There is deliberately no method a server-driven code path
 * can call to enable a screened-out task.
 */
class TaskStore(
    context: Context,
    /** Local tier for an action id; null when this build has no such action. */
    private val tierOf: (String) -> ActionTier?,
    private val now: () -> Long = System::currentTimeMillis
) {

    private val dir = File(context.applicationContext.filesDir, "jarvis")
    private val file = File(dir, FILE_NAME)
    private val mutex = Mutex()
    private val listeners = CopyOnWriteArrayList<(List<TaskDefinition>) -> Unit>()

    /** In-memory copy. The trigger path reads this on every event. */
    @Volatile
    private var cache: List<TaskDefinition> = emptyList()

    @Volatile
    private var loaded = false

    // --- reading ------------------------------------------------------------

    /** Everything, loading from disk the first time. */
    suspend fun all(): List<TaskDefinition> {
        if (!loaded) load()
        return cache
    }

    /** Everything currently switched on and actually runnable. */
    suspend fun enabled(): List<TaskDefinition> = all().filter { it.isRunnable() }

    /** The cached copy without touching disk. Empty before the first [load]. */
    fun snapshot(): List<TaskDefinition> = cache

    suspend fun get(id: String): TaskDefinition? = all().firstOrNull { it.id == id }

    suspend fun load(): List<TaskDefinition> = withContext(Dispatchers.IO) {
        mutex.withLock {
            val root = try {
                if (file.exists()) JSONObject(file.readText()) else null
            } catch (t: Throwable) {
                // A corrupt file must not take the automation layer down with
                // it. Keep the bad copy for the user to look at and start empty.
                Log.w(TAG, "tasks.json is unreadable; parking it", t)
                runCatching { file.copyTo(File(dir, "$FILE_NAME.corrupt"), overwrite = true) }
                null
            }
            val tasks = if (root == null) emptyList() else TaskJson.bundleFromJson(root)
            val userFlags = if (root == null) emptySet() else userEnabledIds(root)
            // enabledByUser is not read from the wire — TaskJson refuses it,
            // because it cannot tell a file we wrote from a payload a server
            // sent. Here we know the source is our own file, so it is restored.
            cache = tasks.map { it.copy(enabledByUser = it.id in userFlags) }
            loaded = true
            cache
        }.also { notifyChanged(it) }
    }

    private fun userEnabledIds(root: JSONObject): Set<String> {
        val arr = root.optJSONArray("tasks") ?: return emptySet()
        val out = LinkedHashSet<String>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val id = o.optString("id").trim()
            if (id.isNotEmpty() && o.optBoolean("enabled_by_user", false)) out.add(id)
        }
        return out
    }

    // --- writing ------------------------------------------------------------

    /**
     * Add or replace one task, applying the screening rules.
     *
     * @param fromServer true when this arrived over the socket. Forces
     *   [TaskSource.SERVER] regardless of what the payload claimed, so a
     *   pushed task cannot label itself local to skip screening.
     */
    suspend fun upsert(task: TaskDefinition, fromServer: Boolean): TaskUpsertResult {
        val previous = get(task.id)
        val source = if (fromServer) TaskSource.SERVER else task.source

        // A server push can never carry the user's consent flag. It survives an
        // edit only when the executable part of the task did not change.
        val carriedUserFlag = when {
            previous == null -> false
            TaskSafety.requiresReconsent(previous, task) -> false
            else -> previous.enabledByUser
        }

        val candidate = task.copy(
            source = source,
            enabledByUser = carriedUserFlag,
            createdAtMs = previous?.createdAtMs?.takeIf { it > 0 } ?: now(),
            updatedAtMs = now()
        )

        val admission = TaskSafety.screen(candidate, tierOf)
        val stored = candidate.copy(enabled = TaskSafety.effectiveEnabled(candidate, admission))

        write { list -> list.filter { it.id != stored.id } + stored }

        if (stored.enabled != candidate.enabled) {
            Log.i(TAG, "task ${stored.id} held disabled: ${admission.reason}")
        }
        return TaskUpsertResult(stored, admission, heldForConsent = !stored.enabled && candidate.enabled)
    }

    /** Replace the whole set — the import path, and how the server syncs. */
    suspend fun replaceAll(tasks: List<TaskDefinition>, fromServer: Boolean): List<TaskUpsertResult> {
        val results = ArrayList<TaskUpsertResult>(tasks.size)
        for (task in tasks) results.add(upsert(task, fromServer))
        // Anything not in the incoming set is gone.
        val keep = tasks.map { it.id }.toSet()
        write { list -> list.filter { it.id in keep } }
        return results
    }

    suspend fun delete(id: String) {
        write { list -> list.filter { it.id != id } }
    }

    suspend fun deleteAll() {
        write { emptyList() }
    }

    /**
     * The user turned a task on or off in the app.
     *
     * This is the ONLY way [TaskDefinition.enabledByUser] becomes true, and the
     * only way a screened-out task ever runs. It takes no argument identifying
     * a caller because there is no caller other than the UI: nothing on the
     * command path can reach it, since the command path has no reference to a
     * `TaskStore` method that sets consent.
     */
    suspend fun setEnabledByUser(id: String, enabled: Boolean): TaskDefinition? {
        var updated: TaskDefinition? = null
        write { list ->
            list.map { task ->
                if (task.id != id) {
                    task
                } else {
                    task.copy(
                        enabled = enabled,
                        enabledByUser = enabled,
                        updatedAtMs = now()
                    ).also { updated = it }
                }
            }
        }
        return updated
    }

    /** Screening for the task list UI: why is this one switched off? */
    suspend fun admissionFor(id: String): TaskAdmission? =
        get(id)?.let { TaskSafety.screen(it, tierOf) }

    // --- import / export ----------------------------------------------------

    /** The whole store as JSON, for the export button and for the server. */
    suspend fun export(): JSONObject = TaskJson.bundleToJson(all())

    /**
     * Import a bundle. Screened exactly like a push: an imported task with a
     * CONFIRM step arrives switched off, whether it came from a server or from
     * a file the user was sent.
     */
    suspend fun import(bundle: JSONObject, fromServer: Boolean): List<TaskUpsertResult> {
        val tasks = TaskJson.bundleFromJson(bundle)
        if (tasks.isEmpty()) return emptyList()
        return tasks.map { upsert(it, fromServer) }
    }

    // --- change notification ------------------------------------------------

    fun addListener(listener: (List<TaskDefinition>) -> Unit) {
        listeners.add(listener)
    }

    fun removeListener(listener: (List<TaskDefinition>) -> Unit) {
        listeners.remove(listener)
    }

    private fun notifyChanged(tasks: List<TaskDefinition>) {
        for (listener in listeners) {
            runCatching { listener(tasks) }
                .onFailure { Log.w(TAG, "task listener failed", it) }
        }
    }

    // --- disk ---------------------------------------------------------------

    private suspend fun write(transform: (List<TaskDefinition>) -> List<TaskDefinition>) {
        if (!loaded) load()
        val next = withContext(Dispatchers.IO) {
            mutex.withLock {
                val updated = transform(cache).sortedBy { it.name.lowercase() }
                persistLocked(updated)
                cache = updated
                updated
            }
        }
        notifyChanged(next)
    }

    private fun persistLocked(tasks: List<TaskDefinition>) {
        try {
            if (!dir.exists()) dir.mkdirs()
            val tmp = File(dir, "$FILE_NAME.tmp")
            tmp.writeText(TaskJson.bundleToJson(tasks).toString(2))
            if (!tmp.renameTo(file)) {
                // Rename can fail on some filesystems; fall back to a direct
                // write, which is worse but still leaves valid JSON.
                file.writeText(TaskJson.bundleToJson(tasks).toString(2))
                tmp.delete()
            }
        } catch (t: Throwable) {
            Log.w(TAG, "could not persist tasks", t)
        }
    }

    /** The file the settings screen can offer to export or share. */
    fun file(): File = file

    companion object {
        private const val TAG = "JarvisTasks"
        private const val FILE_NAME = "tasks.json"
    }
}

/** What [TaskStore.upsert] did, and why. */
data class TaskUpsertResult(
    val task: TaskDefinition,
    val admission: TaskAdmission,
    /**
     * True when the task asked to be enabled and screening refused. The UI
     * shows these as "needs your approval before it can run".
     */
    val heldForConsent: Boolean
)
