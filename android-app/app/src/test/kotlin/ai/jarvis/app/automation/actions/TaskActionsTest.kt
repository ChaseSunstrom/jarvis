package ai.jarvis.app.automation.actions

import ai.jarvis.app.automation.actions.builtin.Builtins
import ai.jarvis.app.automation.actions.builtin.ImportPhoneTasks
import ai.jarvis.app.automation.actions.builtin.ListPhoneTasks
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.automation.tasks.TaskJson
import java.io.File
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * M98's way into PHONE TASKS, proved where a JVM can prove it: the two
 * actions are registered at the tiers the mirror states, a lone `task`
 * becomes a bundle of the current schema, and the skill's own example — the
 * document the house will actually send — parses into a task the store would
 * accept. What a handset does with the consent screen is the M98 gate's fake
 * phone on the house and ADT rows.
 */
class TaskActionsTest {

    @Test
    fun `import_tasks is tier 3 and list_tasks tier 1, both registered once`() {
        val all = Builtins.all()
        assertEquals(1, all.count { it.id == "import_tasks" })
        assertEquals(1, all.count { it.id == "list_tasks" })
        assertEquals(ActionTier.CONFIRM, ImportPhoneTasks.tier)
        assertEquals(ActionTier.AUTO, ListPhoneTasks.tier)
        assertEquals("automation", ImportPhoneTasks.capability)
        assertTrue(ImportPhoneTasks.paramsSchema.keys.containsAll(listOf("bundle", "task")))
    }

    @Test
    fun `a lone task is wrapped as a one-task bundle of the current schema`() {
        val one = JSONObject("""{"id":"t1","name":"T","triggers":["screen_on"],"steps":[{"type":"stop"}]}""")
        val bundle = ImportPhoneTasks.bundleOf(JSONObject().put("task", one))!!
        assertEquals(TaskJson.SCHEMA_VERSION, bundle.getInt("version"))
        assertEquals(1, bundle.getJSONArray("tasks").length())
        assertEquals("t1", TaskJson.bundleFromJson(bundle).single().id)
    }

    @Test
    fun `a bundle is taken as sent, and nothing at all is refused before the store`() {
        val sent = JSONObject("""{"version":1,"tasks":[{"id":"a"},{"id":"b"}]}""")
        assertEquals(sent.toString(), ImportPhoneTasks.bundleOf(JSONObject().put("bundle", sent))!!.toString())
        assertNull(ImportPhoneTasks.bundleOf(JSONObject().put("something", "else")))
    }

    @Test
    fun `the skill's example is a task this phone parses`() {
        // The document the model is taught to send, read from where the house
        // reads it. Not a copy: a copy would pass after the skill drifted.
        val skill = File("../../jarvis-core/config/skills/phone-tasks/SKILL.md")
        assertTrue("the phone-tasks skill is checked in at ${skill.absolutePath}", skill.isFile)
        val example = skill.readText().substringAfter("```json\n").substringBefore("\n```")
        val bundle = ImportPhoneTasks.bundleOf(JSONObject(example))!!
        val tasks = TaskJson.bundleFromJson(bundle)
        assertEquals(1, tasks.size)
        val task = tasks.single()
        assertEquals("torch-on-charge", task.id)
        assertEquals(listOf("power_connected"), task.triggers.map { it.type })
        assertEquals("toggle_torch", task.steps.single().action)
        assertEquals(true, task.steps.single().params["on"])
        // The document's wish is read; the person's decision is not.
        assertTrue(task.enabled)
        assertEquals(false, task.enabledByUser)
    }
}
