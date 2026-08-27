package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.automation.AutomationRuntime
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.automation.tasks.TaskJson
import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * The house's way into PHONE TASKS.
 *
 * `TaskStore.import` existed for the whole life of the task engine and nothing
 * called it: the settings screen said tasks "arrive from jarvis-core over the
 * device channel" and none could. These two actions are that channel — plain
 * `device_command`s, so the house ships a task with the same tool, the same
 * manifest and the same tier rules as a torch or a toast, and nothing new on
 * the wire had to be invented (or secured).
 *
 * What they do NOT do: turn a task on. `enabled` in the document is a request;
 * the store screens every imported task exactly as it screens a file the user
 * was sent, and one with a CONFIRM-tier step arrives switched off whatever the
 * document says. The person turns it on in PHONE TASKS, and only there.
 */
object ImportPhoneTasks : JarvisAction {
    override val id = "import_tasks"

    /**
     * Tier 3. Installing behaviour that runs later, unattended, is something
     * the person sees once: the consent screen shows the task names and the
     * house's reason. The store's screening is the second gate, not a reason
     * to skip this one — screening decides whether a task may START enabled,
     * this decides whether it may be on the phone at all.
     */
    override val tier = ActionTier.CONFIRM
    override val description =
        "Install one or more tasks on this phone — automations the phone runs by itself " +
            "(triggers, conditions, steps). A task with an action that needs confirming " +
            "arrives switched off."
    override val paramsSchema = mapOf(
        "bundle" to "object: {\"version\": 1, \"tasks\": [task, …]} in the phone's task format " +
            "(see the phone-tasks skill)",
        "task" to "object: a single task, instead of a bundle"
    )
    override val capability = "automation"

    /**
     * The document to import: `bundle` as sent, or a lone `task` wrapped as a
     * one-task bundle of the current schema version. Null when neither is
     * there. Pure, so the JVM suite can pin it without a phone.
     */
    internal fun bundleOf(params: JSONObject): JSONObject? =
        params.optJSONObject("bundle")
            ?: params.optJSONObject("task")?.let { one ->
                JSONObject().put("version", TaskJson.SCHEMA_VERSION).put("tasks", JSONArray().put(one))
            }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val bundle = bundleOf(params)
            ?: return ActionResult.error("send a 'bundle' ({version, tasks: […]}) or a 'task'")
        // Parsed once here so an unusable document is refused with a reason
        // rather than imported as nothing: bundleFromJson drops what it cannot
        // read, and "0 tasks imported" would have looked like success.
        val parsed = TaskJson.bundleFromJson(bundle)
        if (parsed.isEmpty()) {
            return ActionResult.error(
                "no usable task in the bundle: each needs an 'id' and at least one of triggers/steps"
            )
        }
        val results = AutomationRuntime.ensure(ctx).tasks.import(bundle, fromServer = true)
        val rows = JSONArray()
        for (r in results) {
            rows.put(
                json(
                    "id" to r.task.id,
                    "name" to r.task.name,
                    "enabled" to r.task.enabled,
                    "held_for_consent" to r.heldForConsent,
                    "reason" to r.admission.reason.ifEmpty { null }
                )
            )
        }
        return ActionResult.ok(
            json(
                "imported" to results.size,
                "held_for_consent" to results.count { it.heldForConsent },
                "tasks" to rows
            )
        )
    }
}

/** What is on this phone, for the house to reason about before it sends more. */
object ListPhoneTasks : JarvisAction {
    override val id = "list_tasks"
    override val tier = ActionTier.AUTO
    override val description = "List the tasks installed on this phone: id, name, whether it is on, what starts it."
    override val paramsSchema = emptyMap<String, String>()
    override val capability = "automation"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val store = AutomationRuntime.ensure(ctx).tasks
        val rows = JSONArray()
        for (task in store.all()) {
            rows.put(
                json(
                    "id" to task.id,
                    "name" to task.name,
                    "enabled" to task.enabled,
                    "source" to task.source.name,
                    "triggers" to JSONArray(task.triggers.map { it.type }),
                    "steps" to task.steps.size
                )
            )
        }
        return ActionResult.ok(json("count" to rows.length(), "tasks" to rows))
    }
}
