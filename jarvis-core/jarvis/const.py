"""Shared constants for Jarvis Core."""

from __future__ import annotations

VERSION = "0.1.0"

# --- event types -----------------------------------------------------------
EVENT_STATE_CHANGED = "state_changed"
EVENT_SERVICE_REGISTERED = "service_registered"
EVENT_CALL_SERVICE = "call_service"
EVENT_JARVIS_START = "jarvis_start"
EVENT_JARVIS_STOP = "jarvis_stop"
EVENT_ENTITY_REGISTRY_UPDATED = "entity_registry_updated"
EVENT_DEVICE_REGISTRY_UPDATED = "device_registry_updated"
EVENT_AREA_REGISTRY_UPDATED = "area_registry_updated"
EVENT_AUTOMATION_TRIGGERED = "automation_triggered"

#: An automation whose action list raised. Fired beside the log line, because a
#: log line on a headless box is not a notification: the 3am automation is the
#: one everybody writes and nobody watches, and the first sign of trouble used
#: to be noticing weeks later that something had stopped happening. An
#: automation can trigger on this to tell you.
EVENT_AUTOMATION_FAILED = "automation_failed"

MATCH_ALL = "*"

# --- common states ---------------------------------------------------------
STATE_ON = "on"
STATE_OFF = "off"
STATE_HOME = "home"
STATE_NOT_HOME = "not_home"
STATE_UNKNOWN = "unknown"
STATE_UNAVAILABLE = "unavailable"
STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_LOCKED = "locked"
STATE_UNLOCKED = "unlocked"
STATE_PLAYING = "playing"
STATE_PAUSED = "paused"
STATE_IDLE = "idle"

# --- domains ---------------------------------------------------------------
DOMAIN_LIGHT = "light"
DOMAIN_SWITCH = "switch"
DOMAIN_SENSOR = "sensor"
DOMAIN_BINARY_SENSOR = "binary_sensor"
DOMAIN_CLIMATE = "climate"
DOMAIN_COVER = "cover"
DOMAIN_LOCK = "lock"
DOMAIN_FAN = "fan"
DOMAIN_MEDIA_PLAYER = "media_player"
DOMAIN_SCENE = "scene"
DOMAIN_SCRIPT = "script"
DOMAIN_AUTOMATION = "automation"
DOMAIN_PERSON = "person"
DOMAIN_BUTTON = "button"
DOMAIN_NUMBER = "number"
DOMAIN_SELECT = "select"
DOMAIN_TEXT = "text"
DOMAIN_TODO = "todo"
DOMAIN_CALENDAR = "calendar"
DOMAIN_WEATHER = "weather"
DOMAIN_NOTIFY = "notify"

# Domains an LLM may control by default (safe, reversible).
SAFE_CONTROL_DOMAINS = frozenset(
    {
        DOMAIN_LIGHT,
        DOMAIN_SWITCH,
        DOMAIN_FAN,
        DOMAIN_COVER,
        DOMAIN_CLIMATE,
        DOMAIN_MEDIA_PLAYER,
        DOMAIN_SCENE,
        DOMAIN_SCRIPT,
        DOMAIN_NUMBER,
        DOMAIN_SELECT,
        DOMAIN_BUTTON,
        DOMAIN_TEXT,
    }
)

# Domains that ALWAYS require explicit human approval (Tier 3).
GATED_DOMAINS = frozenset({DOMAIN_LOCK, DOMAIN_NOTIFY})

#: Individual `domain.service` calls that ALWAYS require explicit human
#: approval, whichever route reaches them.
#:
#: ## Why this is separate from GATED_DOMAINS
#:
#: `GATED_DOMAINS` gates a whole ENTITY domain: every `lock.*` call is
#: dangerous because every entity in it is a door. That is the wrong shape for
#: an integration domain where one service is dangerous and its neighbours are
#: not — `orchestrator.execute` runs a shell command, while
#: `orchestrator.code_status` reads a job's progress. Gating the domain would
#: hold a status poll for a human; gating nothing held a shell command for
#: nobody.
#:
#: ## The hole this closes
#:
#: The tier system decides a TOOL's tier in code, and `execute_command`,
#: `apply_code_task` and a writing `web_browse` batch are all Tier 3. But each
#: of those verbs is ALSO registered as a service, so an automation can call it
#: directly — and `reach.gated_reach` only ever compared a call's *domain*
#: against `GATED_DOMAINS`. `orchestrator` is not an entity domain, so it
#: matched nothing:
#:
#:     automations:
#:       - alias: Tidy up
#:         triggers: [{platform: time, at: "03:00:00"}]
#:         actions: [{service: orchestrator.execute, data: {command: "..."}}]
#:
#: `create_automation` wrote that at Tier 1 and `automation_control` ran it at
#: Tier 1, so the model could author a shell command and then run it with no
#: human in the loop — while calling `execute_command` directly was correctly
#: held. `async_execute` forwards the approval secret *because* "the human has
#: already said yes", a guarantee that only ever held for the tool.
#:
#: This is the same lesson as the plural-key bug in `automation/reach.py`: the
#: analysis is only as good as what it is handed, and two ways to say one thing
#: means one of them gets forgotten. `tests/test_gated_services.py` pins every
#: Tier-3 tool against its service twin so a new one cannot be added without
#: confronting this.
GATED_SERVICES = frozenset(
    {
        # Runs a command in the sandbox. Tool form: `execute_command` (Tier 3).
        "orchestrator.execute",
        # Applies a diff to a real repository. Tool form: `apply_code_task`.
        "orchestrator.code_apply",
        # Drives a real browser. The TOOL escalates only for a batch that
        # clicks or types (`is_write_batch`); the service takes the same steps
        # with no such check. Read-only batches are the minority and a
        # confirmation is the cheap side of this module's rule, so the whole
        # service is held rather than re-deriving "is this a write" here, where
        # a wrong answer is silent.
        "web.browse",
        # Writes a file into one of the user's places. Tool form: `write_file`
        # (Tier 3). The tool asks every time; without this the SERVICE would be
        # the way round it, and "overwrite the note" is a loss with no undo.
        "files.write",
        # Starts a Jarvis Code job, which edits files in a real repository on
        # a branch of its own and runs that repository's checks. Tool form:
        # `start_coding_job` (Tier 3) — NOT `code_task`, which is the
        # orchestrator's remote one at Tier 2 and a different verb
        # entirely. `code.repositories` and `code.result` are its
        # ungated neighbours and read nothing but what the operator configured
        # and what a finished job produced — which is exactly the shape this
        # set exists for, and why it names calls rather than the domain.
        "code.run",
        # Pushes a branch to GitHub or GitLab. Tool form: `push_branch`
        # (Tier 3). Outward-facing in the way this set exists for: it puts
        # code on a server other people can see, and deleting a local file
        # does not undo it. `code.clone_repository` is its ungated neighbour
        # and only reads something the operator's allow-list already permits.
        "code.push_branch",
    }
)

# --- common attributes -----------------------------------------------------
ATTR_FRIENDLY_NAME = "friendly_name"
ATTR_ENTITY_ID = "entity_id"
ATTR_DEVICE_CLASS = "device_class"
ATTR_UNIT_OF_MEASUREMENT = "unit_of_measurement"
ATTR_SUPPORTED_FEATURES = "supported_features"
ATTR_ICON = "icon"
ATTR_AREA_ID = "area_id"
ATTR_DEVICE_ID = "device_id"
ATTR_BRIGHTNESS = "brightness"
ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
ATTR_RGB_COLOR = "rgb_color"
ATTR_TEMPERATURE = "temperature"
ATTR_CURRENT_TEMPERATURE = "current_temperature"
ATTR_HVAC_MODE = "hvac_mode"
ATTR_POSITION = "position"
ATTR_VOLUME_LEVEL = "volume_level"
ATTR_MEDIA_TITLE = "media_title"

# --- services --------------------------------------------------------------
SERVICE_TURN_ON = "turn_on"
SERVICE_TURN_OFF = "turn_off"
SERVICE_TOGGLE = "toggle"
SERVICE_OPEN_COVER = "open_cover"
SERVICE_CLOSE_COVER = "close_cover"
SERVICE_STOP_COVER = "stop_cover"
SERVICE_SET_COVER_POSITION = "set_cover_position"
SERVICE_LOCK = "lock"
SERVICE_UNLOCK = "unlock"
SERVICE_SET_TEMPERATURE = "set_temperature"
SERVICE_SET_HVAC_MODE = "set_hvac_mode"
SERVICE_MEDIA_PLAY = "media_play"
SERVICE_MEDIA_PAUSE = "media_pause"
SERVICE_MEDIA_STOP = "media_stop"
SERVICE_MEDIA_NEXT_TRACK = "media_next_track"
SERVICE_VOLUME_SET = "volume_set"
SERVICE_SELECT_OPTION = "select_option"
SERVICE_SET_VALUE = "set_value"
SERVICE_PRESS = "press"
SERVICE_RELOAD = "reload"
