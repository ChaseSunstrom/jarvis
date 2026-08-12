package ai.jarvis.app.automation.actions

import ai.jarvis.app.compat.RuntimePermissions
import ai.jarvis.app.ui.PermissionBridge
import android.content.Context

/**
 * Seam between the dispatcher and whatever can put an Android permission dialog
 * on screen.
 *
 * The same shape as [ApprovalGateway], for the same reason: `requestPermissions`
 * is a method on `Activity`, every command arrives in a Service, and the
 * automation layer must not have to know which Activity or that there is a UI
 * layer at all.
 *
 * This is the missing half of a bug that hid for the whole life of the app. The
 * actions were written correctly — each re-checks its permission and returns
 * `ActionResult.missingPermission` — and the manifest declared everything. But
 * `SEND_SMS`, `CALL_PHONE`, `READ_CONTACTS`, `READ_CALENDAR`,
 * `WRITE_CALENDAR`, `ACCESS_COARSE/FINE_LOCATION` and `ACTIVITY_RECOGNITION`
 * were never *requested*, so the re-check answered "no" every time, on every
 * device, with no dialog ever shown and nothing anywhere saying why.
 */
interface PermissionGateway {

    /**
     * Which of [permissions] this device does not hold right now, restricted to
     * the ones a dialog could actually grant. A permission that needs a trip to
     * Settings is not reported here — see [RuntimePermissions.missing].
     */
    fun missing(permissions: List<String>): List<String>

    /**
     * Ask the user for [permissions], and answer with the ones still missing.
     *
     * An empty result means all of them are now held. Must never throw and must
     * never claim a grant it did not observe.
     */
    suspend fun request(actionId: String, permissions: List<String>): List<String>
}

/**
 * Gateway for tests and headless builds: it knows of nothing missing and can
 * ask nobody.
 *
 * [missing] answering empty is what keeps the permission step out of the way in
 * a unit test — there is no PackageManager worth consulting there — and
 * [request] returning everything it was given is the fail-closed answer for the
 * case where something asked anyway.
 */
object NoPermissionGateway : PermissionGateway {
    override fun missing(permissions: List<String>): List<String> = emptyList()
    override suspend fun request(actionId: String, permissions: List<String>): List<String> =
        permissions
}

/**
 * THE SECOND PLACE this module touches the UI layer, and for the same reason as
 * the first: only an Activity can ask.
 *
 * `ai.jarvis.app.ui.PermissionBridge` raises a one-frame Activity, requests the
 * permissions, and suspends until the user answers — falling back to a
 * notification when a background activity start is refused, and failing closed
 * on every path where no answer arrives.
 */
class UiPermissionGateway(context: Context) : PermissionGateway {

    private val appContext = context.applicationContext

    override fun missing(permissions: List<String>): List<String> =
        RuntimePermissions.missing(appContext, permissions)

    override suspend fun request(actionId: String, permissions: List<String>): List<String> =
        PermissionBridge.ensure(appContext, actionId, permissions)
}
