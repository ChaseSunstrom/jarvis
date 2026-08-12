#!/usr/bin/env python3
"""Executable spec: a declared permission that nobody requests is a bug.

Reported as *"I asked it to text someone, and it never did, even though it has
correct permissions"*. The planner bug behind half of that is fixed elsewhere
(`contact_resolve_test.py`). This file is about the other half, which was
worse, because it applied to nine permissions rather than one and nothing
anywhere reported it.

`AndroidManifest.xml` opens with a paragraph promising that *"every dangerous
permission is requested at runtime, at the moment it is first needed"*. It was
not true. Every `requestPermissions` call in the app asked for `RECORD_AUDIO`
or `POST_NOTIFICATIONS`. `SEND_SMS`, `CALL_PHONE`, `READ_CONTACTS`,
`READ_CALENDAR`, `WRITE_CALENDAR`, `ACCESS_COARSE_LOCATION`,
`ACCESS_FINE_LOCATION`, `CAMERA` and the `READ_MEDIA_*` pair were declared and
never asked for; `ACTIVITY_RECOGNITION` was checked for by `get_sensors` and
not even declared, so its check could only ever fail.

The reason it survived review is that every individual piece was right:

  * the actions re-check their own permissions and return an honest
    `permission … not granted` — correct, and required by the brief;
  * the manifest declared everything — correct;
  * SYSTEM CHECK reported "Everything is granted" — correct *about the grants
    it listed*, which were the four it knew about.

Nobody owned the gap between them. `requestPermissions` is a method on
`Activity`; every command arrives in a Service; and no test could see the
difference between "declared" and "held" because both halves passed on their
own.

What this pins:

  * the two lists agree — every dangerous permission in the manifest is in
    `RuntimePermissions.ALL` and vice versa;
  * every entry belongs to a checklist row, so "Everything is granted" means it;
  * the dispatcher actually asks, after the consent gate and before execute;
  * the seam has a caller (this bug's twin: `CompanionSpeechHost`, written and
    never constructed — see `speech_host_test.py`);
  * the two permissions that CANNOT be requested from a dialog are not.

Run:  python3 android-app/tools/runtime_permissions_test.py
      python3 -m pytest android-app/tools/runtime_permissions_test.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"

MANIFEST = ANDROID / "app/src/main/AndroidManifest.xml"
TABLE = KOTLIN / "compat/RuntimePermissions.kt"
CHECKLIST = KOTLIN / "compat/GrapheneCompat.kt"
REGISTRY = KOTLIN / "automation/actions/ActionRegistry.kt"
GATEWAY = KOTLIN / "automation/actions/PermissionGateway.kt"
BRIDGE = KOTLIN / "ui/PermissionBridge.kt"
TRAMPOLINE = KOTLIN / "PermissionRequestActivity.kt"
BUILTINS = KOTLIN / "automation/actions/builtin/Builtins.kt"
SYSTEM_CHECK = KOTLIN / "ui/SystemCheckActivity.kt"

#: AOSP's dangerous permissions — the ones that need a runtime grant. Written
#: out rather than derived, because the point of this file is to be an
#: independent statement of what the platform requires: a spec that read the
#: same table the code reads could not catch a permission missing from it.
DANGEROUS = {
    "android.permission.ACCEPT_HANDOVER",
    "android.permission.ACCESS_BACKGROUND_LOCATION",
    "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.ACCESS_MEDIA_LOCATION",
    "android.permission.ACTIVITY_RECOGNITION",
    "android.permission.ADD_VOICEMAIL",
    "android.permission.ANSWER_PHONE_CALLS",
    "android.permission.BLUETOOTH_ADVERTISE",
    "android.permission.BLUETOOTH_CONNECT",
    "android.permission.BLUETOOTH_SCAN",
    "android.permission.BODY_SENSORS",
    "android.permission.CALL_PHONE",
    "android.permission.CAMERA",
    "android.permission.GET_ACCOUNTS",
    "android.permission.NEARBY_WIFI_DEVICES",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.PROCESS_OUTGOING_CALLS",
    "android.permission.READ_CALENDAR",
    "android.permission.READ_CALL_LOG",
    "android.permission.READ_CONTACTS",
    "android.permission.READ_EXTERNAL_STORAGE",
    "android.permission.READ_MEDIA_AUDIO",
    "android.permission.READ_MEDIA_IMAGES",
    "android.permission.READ_MEDIA_VIDEO",
    "android.permission.READ_PHONE_NUMBERS",
    "android.permission.READ_PHONE_STATE",
    "android.permission.READ_SMS",
    "android.permission.RECEIVE_MMS",
    "android.permission.RECEIVE_SMS",
    "android.permission.RECEIVE_WAP_PUSH",
    "android.permission.RECORD_AUDIO",
    "android.permission.SEND_SMS",
    "android.permission.USE_SIP",
    "android.permission.UWB_RANGING",
    "android.permission.WRITE_CALENDAR",
    "android.permission.WRITE_CALL_LOG",
    "android.permission.WRITE_CONTACTS",
    "android.permission.WRITE_EXTERNAL_STORAGE",
}

#: Permissions that look grantable and are not. Each is a Settings trip, and a
#: `requestPermissions` call naming one is refused instantly and for good.
NEVER_IN_A_DIALOG = {
    # Android 11+ refuses to grant this from a dialog at all, and bundling it
    # with foreground location silently drops the WHOLE request.
    "android.permission.ACCESS_BACKGROUND_LOCATION",
    "android.permission.SYSTEM_ALERT_WINDOW",
    "android.permission.WRITE_SETTINGS",
    "android.permission.SCHEDULE_EXACT_ALARM",
    "android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
}


def read(path: Path) -> str:
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def code_only(source: str) -> str:
    """Strip comments, so a promise in a KDoc never satisfies a check."""
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"//[^\n]*", " ", source)


def manifest_permissions() -> dict[str, str | None]:
    """Declared permission -> its `maxSdkVersion`, if it has one."""
    out: dict[str, str | None] = {}
    for block in re.findall(r"<uses-permission\b(.*?)/>", read(MANIFEST), re.S):
        name = re.search(r'android:name="([^"]+)"', block)
        if not name:
            continue
        cap = re.search(r'android:maxSdkVersion="(\d+)"', block)
        out[name.group(1)] = cap.group(1) if cap else None
    return out


def table_entries() -> list[dict[str, str]]:
    """`RuntimePermissions.ALL`, parsed out of the Kotlin."""
    src = read(TABLE)
    body = src.split("val ALL: List<Entry> = listOf(", 1)
    assert len(body) == 2, "RuntimePermissions.ALL is gone"
    entries = []
    for block in re.findall(r"Entry\((.*?)\n        \),", body[1], re.S):
        permission = re.search(r'permission = (?:"([^"]+)"|Manifest\.permission\.(\w+))', block)
        assert permission, f"unparseable entry: {block[:120]}"
        name = permission.group(1) or f"android.permission.{permission.group(2)}"
        group = re.search(r"group = (?:GrapheneCompat\.)?(ID_\w+)", block)
        entries.append(
            {
                "permission": name,
                "group": group.group(1) if group else "",
                "minSdk": (re.search(r"minSdk = (\d+)", block) or [None, "23"])[1],
                "maxSdk": (re.search(r"maxSdk = (\d+)", block) or [None, ""])[1],
                "separately": "separately = true" in block,
                "why": "why = " in block,
            }
        )
    assert entries, "no entries parsed out of RuntimePermissions.ALL"
    return entries


# --- the two lists agree ----------------------------------------------------
def test_every_dangerous_permission_in_the_manifest_is_in_the_table():
    """The check that did not exist. Nine permissions were on one side only."""
    declared = manifest_permissions()
    tabled = {e["permission"] for e in table_entries()}
    missing = sorted(p for p in declared if p in DANGEROUS and p not in tabled)
    assert not missing, (
        "these dangerous permissions are declared and are not in "
        "RuntimePermissions.ALL, so nothing will ever request them and every "
        "action that needs one fails forever with no dialog: " + ", ".join(missing)
    )


def test_every_table_entry_is_declared_in_the_manifest():
    """The opposite slip, and the one `get_sensors` actually had: code that
    checks for a permission the manifest never asked for. `checkSelfPermission`
    answers DENIED for an undeclared permission and `requestPermissions` refuses
    it outright, so the feature is dead and the reason is invisible."""
    declared = manifest_permissions()
    missing = sorted(e["permission"] for e in table_entries() if e["permission"] not in declared)
    assert not missing, f"in the table, not in the manifest: {missing}"


def test_activity_recognition_is_declared():
    """The concrete instance. `GetSensors` returned missingPermission for the
    step counter on every device because the permission was never declared."""
    assert "android.permission.ACTIVITY_RECOGNITION" in manifest_permissions()


def test_the_manifest_max_sdk_matches_the_table():
    """`READ_EXTERNAL_STORAGE` is declared `maxSdkVersion="32"`. Asking for it on
    Android 13 is asking for something this app does not declare there — an
    instant, permanent denial that looks exactly like a user saying no."""
    declared = manifest_permissions()
    for entry in table_entries():
        cap = declared.get(entry["permission"])
        if cap:
            assert entry["maxSdk"] == cap, (
                f"{entry['permission']} is capped at SDK {cap} in the manifest "
                f"and {entry['maxSdk'] or 'uncapped'} in the table"
            )


def test_nothing_ungrantable_is_in_the_ask_path():
    """A request naming one of these is dropped on the floor — and in the case
    of background location it takes every other permission in the bundle with
    it, which is a way to make a working request stop working."""
    for entry in table_entries():
        if entry["permission"] in NEVER_IN_A_DIALOG:
            assert entry["separately"], (
                f"{entry['permission']} cannot be granted from a dialog and is "
                "not marked `separately = true`, so it will be bundled into a "
                "request that the platform then drops entirely"
            )


# --- every permission is on the checklist -----------------------------------
def test_every_permission_belongs_to_a_checklist_row():
    """What made "Everything is granted" a lie. The screen listed the four
    grants it knew about and said everything was fine about the other nine."""
    rows = set(re.findall(r"\n            id = (ID_\w+),", read(CHECKLIST)))
    orphans = sorted({e["group"] for e in table_entries()} - rows)
    assert not orphans, (
        "these permission groups have no row on SYSTEM CHECK, so a permission "
        "can be missing while the screen says everything is granted: "
        + ", ".join(orphans)
    )


def test_the_checklist_probes_every_group():
    """A row is only as good as its `satisfied`. A group with a row and no probe
    would read as permanently granted."""
    src = read(CHECKLIST)
    probes = set(re.findall(r"RuntimePermissions\.groupHeld\(context, (ID_\w+)\)", src))
    action_groups = {
        e["group"] for e in table_entries() if e["group"].startswith("ID_")
    }
    # The microphone and notification rows predate this and have their own
    # probes; everything else must be answered by the table.
    own = {"ID_MICROPHONE", "ID_POST_NOTIFICATIONS"}
    unprobed = sorted(action_groups - probes - own)
    assert not unprobed, f"checklist rows with no permission probe: {unprobed}"


def test_every_entry_says_what_breaks_without_it():
    """The `why` is shown on the row. "For full functionality" helps nobody."""
    for entry in table_entries():
        assert entry["why"], f"{entry['permission']} has no `why`"


# --- the dispatcher actually asks -------------------------------------------
def test_the_dispatcher_asks_for_what_an_action_needs():
    src = re.sub(r"\s+", " ", code_only(read(REGISTRY)))
    assert "val absent = safeMissingPermissions(needed)" in src, (
        "ActionRegistry.dispatch no longer looks at what an action needs"
    )
    assert "val stillMissing = safeRequestPermissions(actionId, absent)" in src, (
        "ActionRegistry.dispatch no longer asks for it"
    )


def test_it_asks_after_the_human_and_before_execute():
    """Ordering, and both halves matter.

    Before the consent gate, the OS would ask "may Jarvis send SMS?" about a
    command the user is about to refuse — and a server sending nonsense would
    have a dialog-spam primitive. After execute is not asking at all.
    """
    src = re.sub(r"\s+", " ", code_only(read(REGISTRY)))
    consent = src.index("if (!verdict.allowsExecution)")
    # The `val stillMissing` form is the execute-time ask. The bare call earlier
    # is the resolver's, which by design runs before the prompt exists — see
    # test_a_resolver_that_needs_a_permission_gets_it_first.
    ask = src.index("val stillMissing = safeRequestPermissions(actionId, absent)")
    revalidate = src.index("if (PolicyEngine.decide(fresh) == Decision.DENY)")
    execute = src.index("withTimeout(action.timeoutMs)")
    assert consent < ask < revalidate < execute, (
        "the permission request must sit between the consent prompt and the "
        "kill-switch re-check, so that a panic hit during the dialog still wins"
    )


def test_the_standing_bans_are_checked_before_any_dialog():
    """Panic must beat a permission dialog as thoroughly as it beats an action.

    Resolution — and therefore the contacts grant it needs — runs before the
    tier is even known, so the tier-independent bans have to be checked earlier
    still. Otherwise a server could raise permission dialogs on a phone whose
    owner has switched automation off.
    """
    src = re.sub(r"\s+", " ", code_only(read(REGISTRY)))
    standing = src.index("if (PolicyEngine.decide(standing) == Decision.DENY)")
    resolve_ask = src.index("val forResolve = action.resolvePermissions")
    resolve = src.index("when (val resolution = safeResolve(action, live))")
    assert standing < resolve_ask < resolve, (
        "the standing bans (panic / master switch / NEVER) must be decided "
        "before anything is resolved or asked for"
    )
    # And it must be the ENGINE deciding, not a second copy of the rules here.
    assert "localTier = ActionTier.CONFIRM" in src, (
        "the pre-gate no longer asks PolicyEngine at CONFIRM/CONFIRM, which is "
        "what makes it exactly the standing bans and nothing else"
    )


def test_a_resolver_that_needs_a_permission_gets_it_first():
    """"Text Sam" needs READ_CONTACTS *before* the consent prompt, because the
    prompt has to show the number rather than the name. Asking afterwards would
    be asking after the resolver has already refused."""
    src = re.sub(r"\s+", " ", code_only(read(REGISTRY)))
    assert "val forResolve = action.resolvePermissions" in src
    comms = code_only(read(KOTLIN / "automation/actions/builtin/CommsActions.kt"))
    for action in ("SendSms", "PlaceCall"):
        body = comms.split(f"object {action} : JarvisAction", 1)
        assert len(body) == 2, f"{action} is gone"
        assert "resolvePermissions" in body[1][:1200], (
            f"{action} resolves a contact name and does not declare "
            "resolvePermissions, so the lookup fails before anything asks"
        )


def test_the_permission_step_fails_open_and_the_request_fails_closed():
    """Not a gate. Policy has already decided; this only chooses whether to
    raise a dialog. A gateway that throws must not turn an approved action into
    a denied one — but a request that throws granted nothing."""
    src = re.sub(r"\s+", " ", code_only(read(REGISTRY)))
    check = src.split("private fun safeMissingPermissions(", 1)
    assert len(check) == 2, "safeMissingPermissions is gone"
    assert "emptyList()" in check[1][:600], "the permission CHECK no longer fails open"
    ask = src.split("private suspend fun safeRequestPermissions(", 1)
    assert len(ask) == 2, "safeRequestPermissions is gone"
    body = ask[1][:700]
    assert "catch (t: CancellationException) { throw t }" in body, (
        "cancelling the turn is not a refusal"
    )
    assert "wanted" in body.split("catch (t: Throwable)", 1)[1][:200], (
        "a throwing permission request must report everything as still missing"
    )


# --- the seam has a caller --------------------------------------------------
def test_the_gateway_is_actually_constructed():
    """The bug this file exists for has a twin in `speech_host_test.py`: an
    interface, an implementation, a usage example, and nothing ever building
    one. A seam with no caller passes every other kind of test."""
    src = code_only(read(BUILTINS))
    assert "UiPermissionGateway(appContext)" in src, (
        "Builtins.standard does not wire a real PermissionGateway, so the app "
        "runs with NoPermissionGateway and asks for nothing — which is exactly "
        "the state that shipped"
    )


def test_the_registry_takes_no_default_gateway():
    """A default would let a construction site forget silently, which is how
    this collaborator came to be absent for the app's whole life."""
    src = code_only(read(REGISTRY))
    ctor = src.split("class ActionRegistry(", 1)
    assert len(ctor) == 2
    head = ctor[1].split(") {", 1)[0]
    assert "permissions: PermissionGateway" in head, "the gateway is not a constructor param"
    assert not re.search(r"permissions: PermissionGateway\s*=", head), (
        "PermissionGateway has a default; every construction site must decide"
    )


def test_the_bridge_can_reach_the_user_when_the_app_is_in_the_background():
    """A device_command arrives with no Activity on screen and background
    activity starts are refused. Without a notification the dialog is
    unreachable and the command fails for a reason nobody can see."""
    src = code_only(read(BRIDGE))
    assert "postNotification(" in src, "the permission prompt has no notification fallback"
    post = src.index("postNotification(app, id, actionId, permissions, intent)")
    start = src.index("app.startActivity(intent)")
    assert post < start, (
        "the notification is posted after the direct start; if the start is "
        "refused there is nothing left to post from"
    )


def test_it_stops_asking_once_the_user_means_it():
    """"Don't ask again" makes `requestPermissions` return instantly with no
    dialog. Re-asking per command would be an invisible Activity flash on every
    single command, forever."""
    src = code_only(read(BRIDGE))
    assert "permanentlyDenied" in src, "a permanent refusal is not remembered"
    assert "shouldShowRequestPermissionRationale" in code_only(read(TRAMPOLINE)), (
        "nothing detects a permanent refusal, so it can never be remembered"
    )
    # ...and not on disk: the user changes their mind in Settings and a stale
    # "no" would outlive the grant.
    assert "SharedPreferences" not in src and "getSharedPreferences" not in src, (
        "the refusal memo is persisted; it must not outlive the process"
    )


def test_the_trampoline_is_declared_and_survives_the_dialog():
    """`android:noHistory` is right for the listen trampoline and fatal here:
    the permission dialog is a separate activity, so a noHistory host is
    finished the moment it loses the foreground and the result lands nowhere."""
    manifest = read(MANIFEST)
    block = re.search(
        r'<activity\s+android:name="\.PermissionRequestActivity".*?/>', manifest, re.S
    )
    assert block, "PermissionRequestActivity is not declared; it can never start"
    body = block.group(0)
    assert "noHistory" not in body, (
        "noHistory finishes this activity when the permission dialog takes the "
        "foreground, and the result is delivered to a dead window"
    )
    assert "showWhenLocked" not in body, (
        "a permission dialog on a locked phone is answered by whoever is "
        "holding it, not by its owner"
    )
    assert "configChanges" in body, (
        "a rotation would recreate the host mid-dialog: a second request racing "
        "the first, and the destroyed one settling the answer as a refusal"
    )
    assert 'android:exported="false"' in body, "only this app may raise a permission dialog"


def test_the_trampoline_refuses_on_a_locked_phone():
    src = code_only(read(TRAMPOLINE))
    assert "isKeyguardLocked" in src, "the trampoline no longer checks the keyguard"


def test_every_settle_path_answers_exactly_once():
    """A dropped answer hangs the dispatch until the timeout; a double answer
    could report a grant for a request that was refused."""
    src = code_only(read(TRAMPOLINE))
    assert "private var settled = false" in src and "if (settled) return" in src
    assert "PermissionBridge.abandon(" in src, (
        "an activity destroyed without an answer leaves the caller waiting"
    )
    bridge = code_only(read(BRIDGE))
    assert "pending.remove(requestId) ?: return" in bridge, (
        "settle is no longer once-only; a stale activity could answer a request "
        "that has already been settled"
    )


# --- the checklist can fix it in place --------------------------------------
def test_the_checklist_asks_rather_than_pointing_at_settings():
    """This screen exists because "find it in Settings" is how the grants got
    missed. A row for a runtime permission must ask for it."""
    src = code_only(read(SYSTEM_CHECK))
    assert "askInPlace(req)" in src, "the checklist rows no longer request anything"
    assert "RuntimePermissions.inGroup(req.id)" in src, (
        "the row does not know which permissions it stands for"
    )
    assert "GrapheneCompat.openSettingsFor(this, req)" in src, (
        "the Settings fallback is gone; special-access rows have nothing else"
    )
    # ...and a row that can no longer ask must not become a dead tap.
    assert "openAppDetails" in src, (
        "after 'don't ask again' the row would do nothing at all when tapped"
    )


def test_the_manifest_tells_the_server_what_is_not_granted_yet():
    """Not the same as `available: false`. An ungranted action is one dialog
    away from working, and marking it unavailable would teach the model never to
    try — so the grant would never be requested and it would never work."""
    src = code_only(read(REGISTRY))
    assert 'entry.put("missing_permissions"' in src, (
        "the device manifest no longer says which permissions are outstanding"
    )
    available = re.search(r'\.put\("available", ([^)]+)\)', src)
    assert available, "the manifest no longer reports availability"
    assert "missing" not in available.group(1), (
        "`available` now folds in the permission state; an ungranted action "
        "would be advertised as impossible rather than as one prompt away"
    )


def main() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # a broken check is a failure, not an abort
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    entries = table_entries()
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({len(entries)} runtime permissions, "
        f"{len({e['group'] for e in entries})} checklist groups)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
