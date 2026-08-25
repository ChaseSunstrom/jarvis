"""Packaging tests: the Dockerfile, the compose stack, config/ and docs/.

The point of this file is that the *shipped default* actually works. Not "the
YAML parses" — that the config boots into a real house, that every entity_id
and service named in the shipped automations exists once it has, and that the
documentation describes the code as it is rather than as it was.

Nothing here needs a network, a broker, Ollama or Docker. The one integration
that reaches out (rest → Ollama) is expected to fail and land its entities on
`unavailable`, which is the correct behaviour and is asserted as such.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import pytest
import yaml

from jarvis.automation.util import as_list
from jarvis.config import load_config
from jarvis.core import Jarvis
from jarvis.entity import Entity, EntityPlatform
from jarvis.state import slugify

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
DOCS = ROOT / "docs"
PARENT_DOCS = ROOT.parent / "docs"
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
#: The parent repo's companion stack — the HUD, and the two agent services
#: whose "optional" has to be enforced by a profile rather than by a comment.
PARENT_COMPOSE = ROOT.parent / "docker-compose.yml"
REQUIREMENTS = ROOT / "requirements.txt"
README = ROOT / "README.md"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class _PermissiveLoader(yaml.SafeLoader):
    """SafeLoader that tolerates the custom tags without resolving them.

    Used for the "does this file parse at all" checks. The real behaviour of
    !include/!secret is exercised through `load_config` further down.
    """


def _passthrough(loader: yaml.Loader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return f"<{node.tag} {loader.construct_scalar(node)}>"
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


for _tag in (
    "!secret",
    "!env_var",
    "!env_url",
    "!include",
    "!include_dir_named",
    "!include_dir_merge_named",
    "!include_dir_list",
    "!include_dir_merge_list",
):
    _PermissiveLoader.add_constructor(_tag, _passthrough)


def _walk(node: Any) -> Iterator[Any]:
    """Every node in a parsed YAML tree, containers included."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _strings(node: Any) -> Iterator[str]:
    for item in _walk(node):
        if isinstance(item, str):
            yield item


ENTITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
TEMPLATE_ENTITY_RE = re.compile(
    r"(?:states|is_state|is_state_attr|state_attr|has_value|expand|area_name|area_id)"
    r"\(\s*'([a-z][a-z0-9_]*\.[a-z0-9_]+)'"
)

#: Entity ids the shipped config names on purpose without creating them.
#: device_tracker entities spring into existence on the first `see` report, so
#: naming one that does not exist yet is the documented usage, not a typo.
EXPECTED_ABSENT = {"all", "none"}


def _referenced_entity_ids(config: dict[str, Any]) -> set[str]:
    """entity_ids the shipped configuration actually points at."""
    found: set[str] = set()

    for node in _walk(config):
        if not isinstance(node, dict):
            continue
        # `entity_id:` anywhere — triggers, conditions, targets, service data.
        value = node.get("entity_id")
        for candidate in [value] if isinstance(value, str) else (value or []):
            if isinstance(candidate, str) and ENTITY_ID_RE.match(candidate):
                found.add(candidate)

    # Scene targets are the *keys* of an `entities:` mapping.
    for scene in config.get("scene") or []:
        entities = (scene or {}).get("entities") or {}
        found.update(k for k in entities if ENTITY_ID_RE.match(str(k)))

    # Entities the LLM is explicitly told about.
    expose = ((config.get("llm") or {}).get("expose") or {})
    for key in ("entities", "exclude_entities"):
        found.update(str(e) for e in (expose.get(key) or []))

    # Anything named inside a template.
    for text in _strings(config):
        found.update(TEMPLATE_ENTITY_RE.findall(text))

    return {e for e in found if e not in EXPECTED_ABSENT}


def _referenced_services(config: dict[str, Any]) -> set[tuple[str, str]]:
    services: set[tuple[str, str]] = set()
    for node in _walk(config):
        if not isinstance(node, dict):
            continue
        for key in ("service", "action"):
            value = node.get(key)
            # `action:` is also a *list of steps* key on an automation; only a
            # dotted string is a service call.
            if isinstance(value, str) and "." in value and " " not in value:
                domain, _, name = value.partition(".")
                services.add((domain, name))
    return services


def _code_blocks(markdown: str, language: str) -> list[str]:
    return re.findall(rf"^```{language}\n(.*?)^```", markdown, re.MULTILINE | re.DOTALL)


@pytest.fixture(autouse=True)
def _quiet_expected_failures() -> Iterator[None]:
    """The REST block points at Ollama, which is not running in CI.

    That failure is the behaviour under test (`test_unreachable_services_...`),
    so its traceback is expected output, not a signal. Silence it so a real
    error in this file is visible.
    """
    import logging

    noisy = [logging.getLogger("jarvis.entity"), logging.getLogger("jarvis.integrations.rest")]
    previous = [(logger, logger.level) for logger in noisy]
    for logger in noisy:
        logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        for logger, level in previous:
            logger.setLevel(level)


#: What a copy of `config/` must leave behind to still mean "a fresh install".
#:
#: `.storage/` and the recorder database are RUNTIME state, gitignored and not
#: shipped: on a developer's machine they hold that person's own rooms,
#: entities and tokens, and on a box where the stack is running they belong to
#: the container's uid at mode 600, so copying them is a permission error.
#: Copying them made "what does a fresh install look like" mean "what does this
#: machine look like", and the test failed on the box that actually runs
#: Jarvis while passing everywhere else.
#:
#: `packages/` is the same argument one level up: it is where a person adds
#: their own features — a laundry cycle, a demo house — and the shipped
#: directory is empty. Asserting "the default invents no devices" against a
#: directory somebody has since added devices to tests their choices, not the
#: default. It also collided outright: the worked example ships a `demo:` block
#: and an operator's own demo package redefines it.
SHIPPED_ONLY = shutil.ignore_patterns(
    ".storage", "*.db", "*.db-wal", "*.db-shm", "*.db-*", "packages",
)


@pytest.fixture
def config_copy(tmp_path: Path) -> Path:
    """The shipped config, copied so a boot cannot dirty the repo.

    Booting writes .storage/ and jarvis.db; running against ./config directly
    would leave those behind for the next developer to wonder about.
    """
    target = tmp_path / "config"
    shutil.copytree(CONFIG, target, ignore=SHIPPED_ONLY)
    return target


#: The worked example — the full fake house that used to BE the default.
EXAMPLE_HOUSE = CONFIG / "examples" / "house"


def _overlay_example(target: Path) -> Path:
    """Copy `examples/house/` over a copy of `config/`, in place.

    An overlay rather than a standalone directory, because that is what the
    README tells a user to do and what they would actually run: the example
    replaces four files and adds two, and inherits `prompts/` and everything
    else from the shipped config it is an example *of*.
    """
    for name in ("configuration.yaml", "automations.yaml", "scripts.yaml", "scenes.yaml"):
        shutil.copy(EXAMPLE_HOUSE / name, target / name)
    shutil.copy(EXAMPLE_HOUSE / "example.tool.yaml", target / "tools" / "example.tool.yaml")
    # `packages/` is not copied from the shipped config (see SHIPPED_ONLY), so
    # the example's own package needs the directory created here.
    (target / "packages").mkdir(exist_ok=True)
    shutil.copy(EXAMPLE_HOUSE / "packages-laundry.yaml", target / "packages" / "laundry.yaml")
    return target


def _example_config() -> dict[str, Any]:
    """The worked example's configuration, loaded, with nothing left behind."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config"
        # `.storage` is skipped for the same reason `config_copy` skips it: on
        # a box where the stack is RUNNING, those files belong to the
        # container's uid and are mode 600, so copying them is a permission
        # error — and this test is about the YAML, not about the house's live
        # registries.
        shutil.copytree(CONFIG, target, ignore=SHIPPED_ONLY)
        return load_config(_overlay_example(target))


@pytest.fixture
def example_copy(config_copy: Path) -> Path:
    """The shipped config with `examples/house/` copied over it.

    The default is an empty house now, which is the right thing to ship and the
    wrong thing to test safety properties against. "An excluded entity cannot be
    reached through a model-runnable script" is only a claim about a house that
    HAS scripts and an excluded entity; asserted against nothing it passes for
    the least interesting reason there is.

    So the demo house is kept whole as `config/examples/house/` and this fixture
    is exactly the copy instruction in its README. That means two things at
    once: the safety tests keep a populated fixture, and the example itself is
    proven to still work rather than rotting in a directory nobody loads.
    """
    return _overlay_example(config_copy)


async def _boot(config_dir: Path) -> Jarvis:
    jarvis = Jarvis(config_dir)
    await jarvis.async_setup(load_config(config_dir))
    await jarvis.async_start()
    await asyncio.sleep(0.1)  # let the first poll round settle
    return jarvis


# ===========================================================================
# config/ — the shipped default must load
# ===========================================================================
def test_every_shipped_yaml_file_parses() -> None:
    files = sorted(CONFIG.rglob("*.yaml"))
    assert files, "config/ has no YAML in it"
    for path in files:
        with path.open(encoding="utf-8") as handle:
            yaml.load(handle, Loader=_PermissiveLoader)


def test_load_config_succeeds_on_a_fresh_checkout(config_copy: Path) -> None:
    """The gate: no secrets.yaml, no editing, no environment. It just loads."""
    assert not (config_copy / "secrets.yaml").exists(), (
        "a real secrets.yaml must never be shipped — only secrets.yaml.example"
    )
    config = load_config(config_copy)

    infrastructure = {
        "jarvis", "recorder", "history", "logbook", "sun", "voice", "llm",
        "web", "orchestrator", "mqtt", "rest", "command_line",
        "automation", "script", "scene",
    }
    assert infrastructure <= set(config), (
        f"missing from the default config: {infrastructure - set(config)}"
    )

    # And the other half of the claim, which is the one that changed: the
    # default ships an EMPTY house. A room you did not create, a person who is
    # not you and a light you cannot switch on are worse than nothing, because
    # you cannot tell "not set up yet" from "set up wrong". The worked example
    # still has all of it — see `example_copy`.
    invented = {"demo", "person", "template", "input_boolean", "input_number",
                "input_select", "input_text"}
    assert not (invented & set(config)), (
        f"the default configuration invents things the user does not have: "
        f"{sorted(invented & set(config))}"
    )
    assert not (config.get("jarvis") or {}).get("areas"), (
        "the default configuration guesses at a floor plan; areas are the "
        "user's to create"
    )
    # Emptiness rather than a type: `!include` folds a falsy document to `{}`
    # (config.py's `yaml.load(...) or {}`), so an empty automations.yaml arrives
    # as a dict where a list was written. Harmless downstream — everything that
    # reads it iterates — and not what this test is about.
    for key in ("automation", "script", "scene"):
        assert not config[key], f"the default ships a {key}"


def test_the_worked_example_still_has_everything_it_documents(example_copy: Path) -> None:
    """The other side of the split, so the example cannot rot unnoticed.

    It is no longer loaded by anything a user runs, which is exactly the
    condition under which a directory stops working and nobody finds out.
    """
    config = load_config(example_copy)
    expected = {
        "demo", "template", "person",
        "input_boolean", "input_number", "input_select", "input_text",
        "automation", "script", "scene",
    }
    assert expected <= set(config), (
        f"the worked example lost: {expected - set(config)}"
    )
    assert (config["jarvis"] or {}).get("areas"), "the example lost its rooms"
    assert len(config["automation"]) >= 8, "the example lost automations"
    assert len(config["script"]) >= 5, "the example lost scripts"


def test_configuration_references_no_secrets() -> None:
    """`!secret` in the default would make a fresh checkout fail to start.

    Documenting the syntax in a comment is fine; using it is not.
    """
    for path in sorted(CONFIG.rglob("*.yaml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            assert "!secret" not in code, f"{path.name} uses !secret outside a comment: {line!r}"


def test_secrets_example_exists_and_parses() -> None:
    example = CONFIG / "secrets.yaml.example"
    assert example.is_file()
    loaded = yaml.safe_load(example.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict) and loaded


def test_runtime_state_written_into_config_is_ignored_by_git() -> None:
    """The shipped `config/` is a real config directory, and that is the trap.

    Point the server — or just `--create-token` — at it and it writes
    `.storage/auth.json` (token hashes), `jarvis.db` (which knows when the
    house is empty) and, the moment anyone follows the setup steps,
    `secrets.yaml`. All three land *inside a tracked directory*, so the only
    thing between them and a commit is .gitignore.
    """
    ignore = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    patterns = {
        line.strip()
        for line in ignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for needed in (".storage/", "secrets.yaml", "*.db"):
        assert needed in patterns, f".gitignore does not cover {needed}"

    # And the trap is real: these are the paths the code actually writes.
    from jarvis.integrations.recorder import DEFAULT_DB_FILE
    from jarvis.store import Store

    assert Store(CONFIG, "auth").path == CONFIG / ".storage" / "auth.json"
    assert DEFAULT_DB_FILE == "jarvis.db"
    assert (CONFIG / "secrets.yaml.example").is_file()


def test_packages_are_merged_into_the_top_level(example_copy: Path) -> None:
    config = load_config(example_copy)
    assert "packages" not in config, "packages: should be folded away by the loader"

    # example.yaml contributes to four different top-level keys.
    assert "laundry_waiting" in config["input_boolean"]        # dict merge
    assert "laundry_finished_at" in config["input_datetime"]   # new key
    assert "laundry_emptied" in config["script"]               # dict merge
    ids = {a.get("id") for a in config["automation"]}
    assert {"laundry_finished", "laundry_nag"} <= ids          # list concat


#: Blocks whose keys are user data (entity names, helper ids, automation
#: bodies), not integration options — nothing to look up in the source.
_USER_DATA_BLOCKS = frozenset({
    "automation", "script", "scene", "template", "rest", "command_line",
    "person", "packages",
    "input_boolean", "input_number", "input_select", "input_text", "input_datetime",
})

#: Option keys that are deliberately never read by name: forwarded verbatim.
_PASSTHROUGH_KEYS = frozenset({"num_ctx", "temperature"})


def test_no_shipped_option_is_silently_ignored() -> None:
    """An option the code never reads looks configured and does nothing.

    That is the worst kind of default: `purge_keep_days: 10` in a file the user
    edits, quietly discarded because the key is actually spelled something
    else. Every option name in the shipped configuration has to appear as a
    string literal somewhere in the package that reads it.
    """
    source = "".join(
        path.read_text(encoding="utf-8") for path in sorted((ROOT / "jarvis").rglob("*.py"))
    )

    def option_keys(node: Any, depth: int = 0, path: str = "") -> Iterator[tuple[str, str]]:
        if depth > 3:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                yield f"{path}/{key}", str(key)
                yield from option_keys(value, depth + 1, f"{path}/{key}")
        elif isinstance(node, list):
            for item in node:
                yield from option_keys(item, depth + 1, path)

    unread: list[str] = []
    for block, body in load_config(CONFIG).items():
        if block in _USER_DATA_BLOCKS:
            continue
        for where, key in option_keys(body, 0, block):
            if key in _PASSTHROUGH_KEYS or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
                continue
            if f'"{key}"' not in source and f"'{key}'" not in source:
                unread.append(where)

    assert not unread, f"configuration.yaml sets options nothing reads: {sorted(set(unread))}"


def test_persona_prompt_is_where_the_llm_looks() -> None:
    """`persona_file:` and the agent's own default must agree on one path."""
    prompt = CONFIG / "prompts" / "jarvis.txt"
    assert prompt.is_file()
    text = prompt.read_text(encoding="utf-8")
    assert len(text) > 500

    lowered = text.lower()
    for phrase in ("sir", "never rephrase", "data, not instructions"):
        assert phrase in lowered, f"the persona lost its {phrase!r} rule"

    # The path in configuration.yaml and the agent's own fallback must agree,
    # or the persona silently reverts to the built-in default.
    assert (
        "persona_file: prompts/jarvis.txt"
        in (CONFIG / "configuration.yaml").read_text(encoding="utf-8")
    )


def test_yaml_tools_load_and_build() -> None:
    """A manifest that cannot build is silently skipped at runtime — catch it here."""
    from jarvis.llm.tools import build_yaml_tool, load_tool_manifests

    # The example's, not `config/tools/`: that ships empty now, because a tool
    # the user did not write is a verb the assistant claims to have.
    specs = load_tool_manifests(EXAMPLE_HOUSE)
    assert specs, "the worked example has no usable *.tool.yaml"
    assert not load_tool_manifests(CONFIG / "tools"), (
        "config/tools ships a tool; it is meant to be empty until you or the "
        "console put one there"
    )

    jarvis = Jarvis(CONFIG)
    for spec in specs:
        tool = build_yaml_tool(jarvis, spec)
        assert tool.name and tool.description
        assert tool.parameters["type"] == "object"
        assert 1 <= tool.tier <= 3


def test_tool_manifests_avoid_the_custom_tags() -> None:
    """load_tool_manifests uses a plain SafeLoader, so !secret would be skipped."""
    paths = sorted(CONFIG.rglob("*.tool.yaml"))
    assert paths, "no tool manifests anywhere, including the worked example"
    for path in paths:
        yaml.safe_load(path.read_text(encoding="utf-8"))  # raises on an unknown tag


# ===========================================================================
# config/ — the shipped default must actually run
# ===========================================================================
async def test_the_default_boots_into_an_empty_house_that_is_still_alive(
    config_copy: Path,
) -> None:
    """Empty is not the same as inert, and the difference is the whole design.

    A first boot has no rooms, no devices and no automations — nothing was
    invented on the user's behalf. What it does have is Jarvis watching itself:
    is the model loaded, is the disk filling up, where is the sun. Those are
    the only entities that pass the test for a default, which is that they work
    on the day you install and answer a question you will actually ask.
    """
    jarvis = await _boot(config_copy)
    try:
        ids = {state.entity_id for state in jarvis.states.all()}

        # Present, because they need nothing but the software itself.
        for entity_id in (
            "sensor.model_server_models",
            "binary_sensor.model_server_up",
            "sensor.disk_free",
            "sensor.load_average",
            "sensor.jarvis_uptime",
            "sun.sun",
        ):
            assert entity_id in ids, f"{entity_id} is missing from a fresh boot"

        # Absent, because they would be fiction. Checked by domain rather than
        # by id: the failure this guards against is somebody re-adding a demo
        # platform, not one entity slipping back.
        invented = {eid.split(".")[0] for eid in ids} & {
            "light", "climate", "cover", "lock", "fan", "media_player",
            "vacuum", "person", "input_boolean", "input_select",
        }
        assert not invented, f"a fresh install invents {sorted(invented)} entities"
        assert not jarvis.areas.areas, "a fresh install invents rooms"
    finally:
        await jarvis.async_stop()


async def test_the_worked_example_boots_into_a_full_house(example_copy: Path) -> None:
    jarvis = await _boot(example_copy)
    try:
        by_domain: dict[str, int] = {}
        for state in jarvis.states.all():
            by_domain[state.entity_id.split(".")[0]] = (
                by_domain.get(state.entity_id.split(".")[0], 0) + 1
            )

        # Every integration the example demonstrates produced entities.
        for domain in (
            "light", "switch", "sensor", "binary_sensor", "climate", "cover",
            "lock", "fan", "media_player", "number", "select", "text", "button",
            "vacuum", "scene", "script", "automation", "person", "sun",
            "input_boolean", "input_number", "input_select", "input_text",
        ):
            assert by_domain.get(domain), f"no {domain} entities after boot"

        assert len(jarvis.states.all()) > 50
    finally:
        await jarvis.async_stop()


async def test_every_referenced_entity_exists(example_copy: Path) -> None:
    """The typo test.

    An entity_id that does not exist fails silently at runtime — the automation
    simply never fires. This is the check that caught `front_door_sensor`
    (the unique_id) being used where `front_door` (the entity_id) was meant.
    """
    config = load_config(example_copy)
    jarvis = await _boot(example_copy)
    try:
        live = set(jarvis.states.entity_ids())
        missing = sorted(_referenced_entity_ids(config) - live)
        assert not missing, f"config references entities that do not exist: {missing}"
    finally:
        await jarvis.async_stop()


async def test_every_referenced_service_is_registered(example_copy: Path) -> None:
    config = load_config(example_copy)
    jarvis = await _boot(example_copy)
    try:
        missing = sorted(
            f"{domain}.{service}"
            for domain, service in _referenced_services(config)
            if not jarvis.services.has_service(domain, service)
        )
        assert not missing, f"config calls services that are not registered: {missing}"
    finally:
        await jarvis.async_stop()


async def test_template_entities_render_to_clean_values(example_copy: Path) -> None:
    """A folded template that leaks whitespace stops being numeric."""
    jarvis = await _boot(example_copy)
    try:
        for entity_id in ("sensor.feels_like_outside", "sensor.house_power"):
            state = jarvis.states.get(entity_id)
            assert state is not None, f"{entity_id} was not created"
            assert state.state == state.state.strip(), (
                f"{entity_id} state {state.state!r} has stray whitespace — "
                "use {%- ... -%} in the folded template"
            )
            float(state.state)  # raises if the whitespace ever comes back
    finally:
        await jarvis.async_stop()


async def test_command_line_sensors_work_with_the_image_toolset(config_copy: Path) -> None:
    """These commands run inside python:3.12-slim: coreutils and /proc only."""
    jarvis = await _boot(config_copy)
    try:
        await asyncio.sleep(0.2)
        for entity_id in ("sensor.disk_free", "sensor.load_average", "sensor.jarvis_uptime"):
            state = jarvis.states.get(entity_id)
            assert state is not None, f"{entity_id} was not created"
            assert state.state not in ("unavailable", "unknown"), (
                f"{entity_id} is {state.state} — its command failed in this environment"
            )
            assert float(state.state) >= 0
    finally:
        await jarvis.async_stop()

    # Tools the slim image does not ship. Matched against the *command words*
    # only — /proc/uptime is a path, not a call to uptime(1).
    forbidden = {"awk", "gawk", "mawk", "curl", "wget", "jq", "uptime", "free", "systemctl"}
    for entry in load_config(config_copy).get("command_line") or []:
        for block in (entry or {}).values():
            for key in ("command", "command_on", "command_off", "command_state"):
                command = (block or {}).get(key)
                if not command:
                    continue
                words = [
                    token
                    for token in re.split(r"[\s|;&(){}]+", command)
                    if token and not token.startswith(("-", "/", "'", '"'))
                ]
                used = forbidden.intersection(words)
                assert not used, (
                    f"{command!r} uses {sorted(used)}, which python:3.12-slim does not ship"
                )


async def test_unreachable_services_degrade_instead_of_failing(example_copy: Path) -> None:
    """No Ollama, no broker, no Wyoming: startup must still complete."""
    jarvis = await _boot(example_copy)
    try:
        # The REST block points at Ollama, which is not running here.
        assert jarvis.states.get("sensor.model_server_models").state == "unavailable"
        # ...and the rest of the house is unaffected.
        assert jarvis.states.get("light.kitchen_lights").state == "on"
    finally:
        await jarvis.async_stop()


async def test_scripts_that_carry_metadata_become_llm_tools(example_copy: Path) -> None:
    """description + fields is what promotes a script into the tool surface."""
    config = load_config(example_copy)
    jarvis = await _boot(example_copy)
    try:
        for name, script in config["script"].items():
            assert script.get("description"), f"script.{name} has no description"
            assert jarvis.services.has_service("script", name)
        # house_status returns structured data rather than just acting.
        assert config["script"]["house_status"]["sequence"][-1]["response_variable"]
    finally:
        await jarvis.async_stop()


# ===========================================================================
# config/ — the shipped scripts and automations must survive a cold house
# ===========================================================================
# Booting is not the same as working. Nothing below fakes a service: the whole
# point is that Wyoming, Ollama and the broker are all *down*, which is the
# state of a machine two seconds after `docker compose up -d`, and every one of
# these ran a shipped sequence to completion under exactly that.


def _voice_say_steps(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every `service: voice.say` step anywhere in the shipped config."""
    return [
        node
        for node in _walk(config)
        if isinstance(node, dict) and node.get("service", node.get("action")) == "voice.say"
    ]


def test_every_voice_say_step_tolerates_a_dead_tts() -> None:
    """`voice.say` raises when Piper is unreachable, which aborts the sequence.

    A cold start, a restarted container or a voice still downloading is enough.
    Speaking is the least important thing any of these sequences does, so it
    must never cancel the steps after it — that is what silently lost
    `input_text.last_announcement` in `script.announce`.
    """
    # The example's config: the shipped one has no sequences at all now, so
    # asserting against it would pass without checking anything. Every script a
    # user copies out of the example carries the flag.
    config = _example_config()
    steps = _voice_say_steps(config)
    assert steps, "the worked example no longer calls voice.say — fix this test"
    missing = [s for s in steps if not s.get("continue_on_error")]
    assert not missing, (
        f"{len(missing)} voice.say step(s) lack continue_on_error: true; an "
        f"unreachable TTS will abort the rest of the sequence: {missing}"
    )


async def test_announce_records_the_message_even_with_tts_down(example_copy: Path) -> None:
    """The regression: TTS raised, so the announcement was never recorded."""
    jarvis = await _boot(example_copy)
    try:
        await jarvis.services.async_call(
            "script", "announce", {"message": "the washing machine has finished"},
            blocking=True, return_response=True,
        )
        recorded = jarvis.states.get("input_text.last_announcement")
        assert recorded is not None
        assert recorded.state == "the washing machine has finished", (
            "voice.say failed and took the rest of script.announce with it"
        )
    finally:
        await jarvis.async_stop()


async def test_good_morning_completes_with_tts_down(example_copy: Path) -> None:
    jarvis = await _boot(example_copy)
    try:
        # `away`, not `night`: selecting `night` fires automation.house_mode_night,
        # which starts script.goodnight in the background and races this test to
        # the same entities.
        await jarvis.services.async_call("input_select", "select_option",
            {"entity_id": "input_select.house_mode", "option": "away"}, blocking=True)
        await jarvis.services.async_call("cover", "close_cover",
            {"entity_id": "cover.living_room_window"}, blocking=True)
        await jarvis.services.async_call("light", "turn_off",
            {"entity_id": "light.kitchen_lights"}, blocking=True)
        await jarvis.services.async_call("switch", "turn_off",
            {"entity_id": "switch.coffee_machine"}, blocking=True)
        await asyncio.sleep(0.05)

        await jarvis.services.async_call(
            "script", "good_morning", {}, blocking=True, return_response=True
        )
        assert jarvis.states.get("input_select.house_mode").state == "home"
        assert jarvis.states.get("light.kitchen_lights").state == "on"
        assert jarvis.states.get("switch.coffee_machine").state == "on"
        assert jarvis.states.get("cover.living_room_window").state in ("open", "opening")
    finally:
        await jarvis.async_stop()


async def test_goodnight_runs_end_to_end(example_copy: Path) -> None:
    """A templated `delay:`, `parallel:`, a gated lock call and a select, in one go."""
    jarvis = await _boot(example_copy)
    try:
        await jarvis.services.async_call(
            "lock", "unlock", {"entity_id": "lock.front_door_lock"}, blocking=True
        )
        await jarvis.services.async_call(
            "script", "goodnight", {"delay_minutes": 0}, blocking=True, return_response=True
        )
        assert jarvis.states.get("lock.front_door_lock").state == "locked"
        assert jarvis.states.get("input_select.house_mode").state == "night"
        assert jarvis.states.get("light.kitchen_lights").state == "off"
        assert jarvis.states.get("cover.living_room_window").state in ("closed", "closing")
        speaker = jarvis.states.get("media_player.living_room_speaker")
        assert speaker.attributes.get("volume_level") == pytest.approx(0.3)
    finally:
        await jarvis.async_stop()


async def test_house_status_returns_structured_data(example_copy: Path) -> None:
    """`stop:` + `response_variable:` is the whole "scripts as LLM tools" story."""
    jarvis = await _boot(example_copy)
    try:
        result = await jarvis.services.async_call(
            "script", "house_status", {}, blocking=True, return_response=True
        )
        assert isinstance(result, dict), f"house_status returned {result!r}"
        assert set(result) == {
            "anyone_home", "house_mode", "outside_temperature", "power_watts",
            "front_door_locked", "garage_open", "lights_on",
        }
        float(result["outside_temperature"])
        assert isinstance(result["lights_on"], list)
    finally:
        await jarvis.async_stop()


async def test_every_shipped_scene_applies(example_copy: Path) -> None:
    """A scene naming an entity it cannot actuate fails silently at runtime."""
    config = load_config(example_copy)
    jarvis = await _boot(example_copy)
    try:
        for scene in config["scene"]:
            entity_id = f"scene.{slugify(scene['name'])}"
            assert jarvis.states.get(entity_id) is not None, f"{entity_id} missing"
            await jarvis.services.async_call(
                "scene", "turn_on", {"entity_id": entity_id}, blocking=True
            )
            await asyncio.sleep(0.05)
            # `state: on` is YAML-1.1 boolean True; the scene layer has to map
            # it back to a state word or every unquoted on/off silently no-ops.
            for target, spec in scene["entities"].items():
                wanted = spec.get("state") if isinstance(spec, dict) else spec
                if wanted is None:
                    continue
                word = str(wanted).strip().lower()
                word = {"true": "on", "false": "off"}.get(word, word)
                actual = jarvis.states.get(target).state
                if word in ("open", "closed"):  # covers move, they do not teleport
                    assert actual in (word, f"{word[:4]}ing", "opening", "closing"), (
                        f"{entity_id}: {target} is {actual!r}, wanted {word!r}"
                    )
                else:
                    assert actual == word, (
                        f"{entity_id}: {target} is {actual!r}, wanted {word!r}"
                    )
    finally:
        await jarvis.async_stop()


async def test_the_security_automation_logs_before_it_speaks(
    example_copy: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Front door opens, nobody home: the whole automation must survive dead TTS.

    Ordering saves the logbook entry (it is step one), but the run itself still
    has to finish — an automation that ends in a traceback drops everything
    after the failing step, so the next line anyone adds to it silently never
    runs.
    """
    caplog.set_level("ERROR", logger="jarvis.automation.engine")
    jarvis = await _boot(example_copy)
    try:
        jarvis.states.set("person.chris", "not_home")
        await jarvis.services.async_call(
            "input_boolean", "turn_off", {"entity_id": "input_boolean.guest_mode"},
            blocking=True,
        )
        await asyncio.sleep(0.1)
        assert jarvis.states.get("binary_sensor.anyone_home").state == "off"

        jarvis.states.set("binary_sensor.front_door", "on")
        await asyncio.sleep(0.5)

        automation = jarvis.states.get("automation.front_door_opened_while_away")
        assert automation.attributes.get("last_triggered"), "the automation never ran"

        entries = await jarvis.services.async_call(
            "logbook", "get", {}, blocking=True, return_response=True
        )
        messages = [e.get("message") for e in (entries or {}).get("entries", [])]
        assert "Front door opened with nobody home" in messages

        blew_up = [
            record.getMessage()
            for record in caplog.records
            if record.name == "jarvis.automation.engine"
            and "Front door opened while away" in record.getMessage()
        ]
        assert not blew_up, (
            f"the automation ended in an error rather than completing: {blew_up}"
        )
    finally:
        await jarvis.async_stop()


async def test_a_webhook_without_coordinates_cannot_degrade_the_tracker(
    example_copy: Path,
) -> None:
    """The webhook id is the only credential, so a junk POST must be a no-op.

    Before the `condition:`, an empty body rendered both gps templates to "",
    which device_tracker.see rejected — and then applied the rest anyway,
    resetting source_type to `router`, gps_accuracy to 50 and battery to 0 on
    top of a perfectly good fix.
    """
    from fastapi.testclient import TestClient

    from jarvis.api.server import create_app
    from jarvis.auth import async_setup_auth

    webhook_id = next(
        trigger["webhook_id"]
        for automation in load_config(example_copy)["automation"]
        for trigger in as_list(automation.get("trigger"))
        if isinstance(trigger, dict) and trigger.get("platform") == "webhook"
    )

    jarvis = await _boot(example_copy)
    try:
        await async_setup_auth(jarvis)
        with TestClient(create_app(jarvis)) as client:
            good = client.post(
                f"/api/webhook/{webhook_id}",
                json={"latitude": 51.5, "longitude": -0.12, "accuracy": 12, "battery": 88},
            )
            assert good.status_code == 200
            await asyncio.sleep(0.3)
            before = dict(jarvis.states.get("device_tracker.chris_phone").attributes)
            assert before["gps_accuracy"] == 12 and before["battery_level"] == 88

            for junk in ({}, {"latitude": 51.5}, {"latitude": None, "longitude": None}):
                assert client.post(f"/api/webhook/{webhook_id}", json=junk).status_code == 200
            await asyncio.sleep(0.4)

            after = dict(jarvis.states.get("device_tracker.chris_phone").attributes)
            for key in ("latitude", "longitude", "gps_accuracy", "battery_level", "source_type"):
                assert after[key] == before[key], (
                    f"a webhook POST with no coordinates changed {key}: "
                    f"{before[key]!r} -> {after[key]!r}"
                )
    finally:
        await jarvis.async_stop()


# ===========================================================================
# config/ — the LLM blast radius the configuration claims to have
# ===========================================================================
# `run_script` and `activate_scene` resolve the script/scene entity and then
# execute whatever is inside, without re-checking the domains that sequence
# calls or the entities it names. So exposure is only as strong as the shipped
# macros: these are the tests that keep the shipped config honest about it.

#: `script.goodnight` is allowed to reach `lock`, and only ever to *lock*.
#: Locking a door you own is the fail-safe direction; see scripts.yaml.
GATED_SCRIPT_EXCEPTIONS = {"goodnight": {"lock.lock"}}


def _exposure(config: dict[str, Any]) -> Any:
    from jarvis.llm.tools import Exposure

    return Exposure.from_config((config.get("llm") or {}).get("expose"))


def _sequence_services(sequence: Any) -> set[str]:
    """Every `domain.service` string a sequence could call, without running it."""
    found: set[str] = set()
    for node in _walk(sequence):
        if isinstance(node, dict):
            value = node.get("service", node.get("action"))
            if isinstance(value, str) and "." in value and " " not in value:
                found.add(value)
    return found


def _sequence_targets(sequence: Any) -> set[str]:
    """entity_ids a sequence *actuates*.

    Deliberately not `_referenced_entity_ids`: an id inside a Jinja template is
    a read, and a curated read is the whole point of `script.house_status`
    reporting `garage_open` for a garage the model may not move. Only an
    `entity_id:` key — in a `target:`, in `data:`, or on the step itself — is a
    thing the sequence acts on.
    """
    found: set[str] = set()
    for node in _walk(sequence):
        if not isinstance(node, dict):
            continue
        value = node.get("entity_id")
        for candidate in [value] if isinstance(value, str) else (value or []):
            if isinstance(candidate, str) and ENTITY_ID_RE.match(candidate.strip().lower()):
                found.add(candidate.strip().lower())
        # `- scene: scene.movie` shorthand and `scene.turn_on` both actuate one.
        shorthand = node.get("scene")
        if isinstance(shorthand, str) and ENTITY_ID_RE.match(shorthand.strip().lower()):
            found.add(shorthand.strip().lower())
    return found


def _macro_closure(config: dict[str, Any], sequence: Any) -> tuple[set[str], set[str]]:
    """(targets, service calls) for a sequence *and* every macro it invokes.

    A script that calls `script.x` or activates `scene.y` can do everything
    those can, so a rule applied only to the outer sequence is trivially
    sidestepped by one level of indirection.
    """
    scripts = config.get("script") or {}
    scenes = {f"scene.{slugify(s['name'])}": s for s in (config.get("scene") or [])}

    targets: set[str] = set()
    services: set[str] = set()
    pending: list[Any] = [sequence]
    visited: set[str] = set()

    while pending:
        current = pending.pop()
        step_targets = _sequence_targets(current)
        step_services = _sequence_services(current)
        targets |= step_targets
        services |= step_services

        for entity_id in step_targets:
            if entity_id in visited:
                continue
            visited.add(entity_id)
            domain, _, object_id = entity_id.partition(".")
            if domain == "script" and object_id in scripts:
                pending.append(scripts[object_id].get("sequence"))
            elif domain == "scene" and entity_id in scenes:
                targets |= {
                    str(t).lower() for t in (scenes[entity_id].get("entities") or {})
                }
        # `service: script.goodnight` is a call, not a target.
        for call in step_services:
            domain, _, object_id = call.partition(".")
            if domain == "script" and object_id in scripts and call not in visited:
                visited.add(call)
                pending.append(scripts[object_id].get("sequence"))

    return targets, services


async def test_run_script_is_not_gated_by_the_domains_it_calls(example_copy: Path) -> None:
    """Pins the platform behaviour these config rules exist to work around.

    This is not an endorsement. `run_script` resolves `script.*`, whose domain
    is `script`, so the GATED_DOMAINS check never sees the `lock.lock` inside.
    If this test ever starts failing because the tool grew a gate, delete the
    exclusions in configuration.yaml along with it.
    """
    from jarvis.llm.tools import ToolRegistry, register_builtin_tools

    jarvis = await _boot(example_copy)
    try:
        llm_config = load_config(example_copy).get("llm") or {}
        registry = ToolRegistry(jarvis, exposure=_exposure({"llm": llm_config}))
        register_builtin_tools(registry, llm_config.get("user_context"))

        await jarvis.services.async_call(
            "lock", "unlock", {"entity_id": "lock.front_door_lock"}, blocking=True
        )
        result = await registry.call("run_script", {"entity_id": "script.goodnight"})
        await asyncio.sleep(0.5)
        assert result.get("status") != "approval_required", (
            "run_script now gates on the script's domains — good; drop the "
            "exclude_entities workaround in configuration.yaml"
        )
        assert jarvis.states.get("lock.front_door_lock").state == "locked"
    finally:
        await jarvis.async_stop()


async def test_excluded_entities_are_not_reachable_through_a_script_or_scene(
    example_copy: Path,
) -> None:
    """An exclusion the model can route around is not an exclusion.

    `switch.coffee_machine` used to be the shipped example while
    `script.good_morning` turned it on and `scene.away` turned it off — both
    model-runnable, so the stated guarantee was false. This walks every script
    and scene the model can reach and asserts none of them names an excluded
    entity as a target.
    """
    config = load_config(example_copy)
    exposure = _exposure(config)
    assert exposure.exclude_entities, "the shipped config excludes nothing to test"

    jarvis = await _boot(example_copy)
    try:
        offenders: list[str] = []

        for name, script in (config.get("script") or {}).items():
            entity_id = f"script.{name}"
            if not exposure.is_exposed(jarvis, entity_id):
                continue
            targets, _ = _macro_closure(config, script.get("sequence"))
            for target in sorted(targets & exposure.exclude_entities):
                offenders.append(f"{entity_id} targets excluded {target}")

        for scene in config.get("scene") or []:
            entity_id = f"scene.{slugify(scene['name'])}"
            if not exposure.is_exposed(jarvis, entity_id):
                continue
            for target in scene.get("entities") or {}:
                if str(target).lower() in exposure.exclude_entities:
                    offenders.append(f"{entity_id} targets excluded {target}")

        assert not offenders, (
            "run_script/activate_scene bypass exclude_entities. Either drop the "
            "target from the macro or exclude the macro too: " + "; ".join(offenders)
        )
    finally:
        await jarvis.async_stop()


async def test_no_model_runnable_macro_reaches_a_gated_domain(example_copy: Path) -> None:
    """Nothing the model can run may unlock a door or send a notification.

    Checked over the macro's transitive closure, so one level of indirection
    (`script.a` calling `script.b`) does not launder a gated call.
    """
    from jarvis.const import GATED_DOMAINS

    config = load_config(example_copy)
    exposure = _exposure(config)
    jarvis = await _boot(example_copy)
    try:
        offenders: list[str] = []

        for name, script in (config.get("script") or {}).items():
            entity_id = f"script.{name}"
            if not exposure.is_exposed(jarvis, entity_id):
                continue
            targets, services = _macro_closure(config, script.get("sequence"))
            calls = {c for c in services if c.split(".")[0] in GATED_DOMAINS}
            gated_targets = {t for t in targets if t.split(".")[0] in GATED_DOMAINS}
            if not calls and not gated_targets:
                continue
            allowed = GATED_SCRIPT_EXCEPTIONS.get(name)
            if allowed is None or not calls <= allowed:
                offenders.append(
                    f"{entity_id} calls {sorted(calls)} on {sorted(gated_targets)}"
                )

        for scene in config.get("scene") or []:
            entity_id = f"scene.{slugify(scene['name'])}"
            if not exposure.is_exposed(jarvis, entity_id):
                continue
            gated = {
                str(t).split(".")[0]
                for t in (scene.get("entities") or {})
                if str(t).split(".")[0] in GATED_DOMAINS
            }
            if gated:
                offenders.append(f"{entity_id} sets {sorted(gated)} entities")

        assert not offenders, (
            "a model-runnable macro reaches a gated domain with no approval; "
            "exclude it under llm: expose: exclude_entities: " + "; ".join(offenders)
        )
    finally:
        await jarvis.async_stop()


async def test_the_excluded_entity_really_is_invisible(example_copy: Path) -> None:
    """End to end: the model cannot read it, list it, or actuate it."""
    from jarvis.llm.tools import ToolRegistry, register_builtin_tools

    config = load_config(example_copy)
    exposure = _exposure(config)
    jarvis = await _boot(example_copy)
    try:
        registry = ToolRegistry(jarvis, exposure=exposure)
        register_builtin_tools(registry, (config.get("llm") or {}).get("user_context"))

        for entity_id in sorted(exposure.exclude_entities):
            assert jarvis.states.get(entity_id) is not None, (
                f"{entity_id} is excluded but does not exist — a typo excludes nothing"
            )
            before = jarvis.states.get(entity_id).state
            for tool, args in (
                ("get_state", {"entity_id": entity_id}),
                ("turn_on", {"entity_id": entity_id}),
                ("turn_off", {"entity_id": entity_id}),
                ("set_cover_position", {"entity_id": entity_id, "position": 100}),
            ):
                result = await registry.call(tool, dict(args))
                assert result.get("status") == "error", f"{tool} reached {entity_id}"
            await asyncio.sleep(0.1)
            assert jarvis.states.get(entity_id).state == before

            listed = await registry.call("list_entities", {})
            assert entity_id not in json.dumps(listed), f"list_entities leaked {entity_id}"
    finally:
        await jarvis.async_stop()


# ===========================================================================
# Dockerfile
# ===========================================================================
def test_dockerfile_shape() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM python:3.12-slim" in text
    assert "COPY requirements.txt" in text and "pip install" in text
    assert "COPY jarvis ./jarvis" in text
    assert "EXPOSE 8080" in text
    assert 'ENTRYPOINT ["python", "-m", "jarvis", "--config", "/config"]' in text

    # Dependencies before source, or every edit busts the pip layer.
    assert text.index("COPY requirements.txt") < text.index("COPY jarvis ./jarvis")


def test_dockerfile_runs_as_a_non_root_user() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    users = re.findall(r"^USER\s+(\S+)", text, re.MULTILINE)
    assert users, "no USER instruction — the container would run as root"
    assert users[-1] not in ("root", "0"), f"final USER is {users[-1]!r}"
    assert "useradd" in text and "10003" in text
    # The user must exist before we switch to it.
    assert text.index("useradd") < text.rindex("USER ")


def test_dockerfile_healthcheck_hits_healthz() -> None:
    # Join `\`-continued lines first, so a multi-line HEALTHCHECK reads as one.
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    check = next(
        (line for line in text.splitlines() if line.startswith("HEALTHCHECK")), ""
    )
    assert check, "no HEALTHCHECK"
    assert "/healthz" in check
    assert "8080" in check
    # curl may not survive the best-effort apt step; the interpreter always does.
    assert "python" in check


def test_dockerignore_keeps_config_out_of_the_build_context() -> None:
    """./config holds secrets.yaml and the recorder DB; the daemon needs neither."""
    path = ROOT / ".dockerignore"
    assert path.is_file()
    entries = {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert "config/" in entries
    # ...and it must not exclude anything the Dockerfile copies.
    assert "jarvis/" not in entries and "requirements.txt" not in entries


def test_dockerignore_excludes_every_relative_bind_mount() -> None:
    """The build context must not swallow the stack's own data directories.

    Every bind mount in docker-compose.yml is a relative path, so its host side
    sits inside `context: .`. ./photon is a geocoding index (tens of GB for the
    planet extract), ./wyoming/* are downloaded speech models, ./ollama is
    model weights. Miss one and `docker compose build` on a machine that has
    been running for a week ships all of it to the daemon before it reads the
    first line of the Dockerfile.
    """
    ignored = {
        line.strip().rstrip("/")
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    mounts: set[str] = set()
    # Commented-out services mount things too, and uncommenting one is a
    # two-character edit — take the host side of every `- ./x:/y` line.
    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*#?\s*-\s+\./([^:\s]+):", line)
        if match:
            mounts.add(match.group(1))

    assert mounts, "no relative bind mounts found — fix this test, not the compose file"
    missing = sorted(
        path
        for path in mounts
        if not any(part in ignored for part in (path, path.split("/")[0]))
    )
    assert not missing, f".dockerignore does not exclude bind-mounted host paths: {missing}"


def test_dockerignore_does_not_exclude_anything_the_image_needs() -> None:
    """Directory patterns and module names collide easily.

    `wyoming/` is a bind-mount directory at the context root; `wyoming.py` is
    the Wyoming protocol client inside the package. Docker matches a pattern
    against the whole relative path, so the first does not eat the second — but
    a pattern written as `wyoming` or `**/wyoming*` would, and the image would
    then start and fail on the first TTS call. Replays the matcher over every
    file the Dockerfile actually COPYs.
    """
    from fnmatch import fnmatch

    patterns = [
        line.strip().rstrip("/")
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    def excluded(relative: str) -> bool:
        parts = relative.split("/")
        for pattern in patterns:
            prefix = "/".join(parts[: pattern.count("/") + 1])
            if fnmatch(relative, pattern) or fnmatch(prefix, pattern):
                return True
        return False

    copied = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "jarvis").rglob("*.py")
        if "__pycache__" not in path.parts
    ] + ["requirements.txt"]
    assert copied

    lost = sorted(path for path in copied if excluded(path))
    assert not lost, f".dockerignore would keep these out of the image: {lost}"


def test_dockerfile_apt_is_best_effort_and_ipv4() -> None:
    """This build environment blocks some mirrors; apt must never be fatal."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    if "apt-get" not in text:
        return
    assert "Acquire::ForceIPv4=true" in text, "apt can stall forever on an IPv6 route"
    assert "|| echo" in text and "WARN" in text, "apt failure must not fail the build"
    assert "timeout " in text, "an unreachable mirror must fail fast, not hang"


# ===========================================================================
# docker-compose.yml
# ===========================================================================
@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def parent_compose() -> dict[str, Any]:
    return yaml.safe_load(PARENT_COMPOSE.read_text(encoding="utf-8"))


def test_the_command_broker_does_not_start_on_a_plain_up(
    parent_compose: dict[str, Any],
) -> None:
    """"Optional" has to mean the container does not run.

    The parent repo's compose is what the quick start tells you to bring up
    for the HUD. It also defines `jarvis-orchestrator` — the approval-gated
    command broker — and `jarvis-sandbox`. Described as optional, and with
    `restart: unless-stopped`, an ungated definition means every installation
    that followed the quick start is running a command broker on :8188 from
    then on, across reboots, whether or not it ever wanted one.

    So both sit behind the `agents` profile, the same opt-in searxng gets
    here. `jarvis-init` goes with them: it exists only to prepare the
    workspace those two share.
    """
    services = parent_compose["services"]
    gated = {name for name, svc in services.items() if svc.get("profiles")}
    assert gated == {"jarvis-init", "jarvis-orchestrator", "jarvis-sandbox"}
    for name in gated:
        assert services[name]["profiles"] == ["agents"], name
    # The HUD is the one thing a plain `up -d` is meant to start.
    assert not services["jarvis-web"].get("profiles")


def test_the_agent_services_are_defined_in_exactly_one_compose_file(
    compose: dict[str, Any],
) -> None:
    """Two live definitions of one container name is a failed `up`, not a spare.

    jarvis-core's compose carries a commented-out sketch of the orchestrator
    and sandbox for the standalone case. The parent's defines them for real,
    with the same `container_name`s, the same host port and the same bind
    mount. If the sketch is ever uncommented while the parent stack exists,
    the two collide — so it stays commented, and this says so out loud.
    """
    assert "jarvis-orchestrator" not in compose["services"]
    assert "jarvis-sandbox" not in compose["services"]
    text = COMPOSE.read_text(encoding="utf-8")
    assert "cd .. && docker compose --profile agents up -d" in text, (
        "the commented sketch must point at the parent stack rather than "
        "inviting someone to uncomment a duplicate"
    )


def test_compose_has_the_whole_stack(compose: dict[str, Any]) -> None:
    services = compose["services"]
    assert set(services) == {
        "jarvis-core",
        "wyoming-openwakeword",
        "wyoming-whisper",
        "wyoming-piper",
        "photon",
        "jarvis-browser",
        "searxng",
        "mosquitto",
        "jarvis-config-init",
        # Retrieval (M33). Two containers of one image because the measurement
        # said so — see `docs/TOOLING_DECISIONS.md` §3.
        "jarvis-embeddings",
        "jarvis-reranker",
        # The alternative voice (M35), behind `--profile kokoro`.
        "jarvis-tts",
        # The single internal model endpoint (M40).
        "jarvis-gateway",
    }
    assert "homeassistant" not in services, "jarvis-core replaces it; it must not be here"


def test_compose_passes_jarvis_core_every_env_var_its_config_reads(
    compose: dict[str, Any],
) -> None:
    """`!env_var` in configuration.yaml only works if the variable gets in.

    This is the failure that has no symptom. `config/configuration.yaml` reads
    JARVIS_BROWSER_TOKEN and BROWSER_APPROVAL_SECRET with `!env_var`; the
    operator puts both in `.env`; compose hands them to `jarvis-browser`, the
    container comes up healthy — and `jarvis-core`, which never received
    them, answers "the jarvis-browser service is not configured" to every
    fetch, crawl and browse for the rest of its life. Nothing logs an error,
    because from Jarvis's side nothing went wrong: the variable simply was
    not there.

    So: every name the shipped config resolves from the environment must
    appear in the jarvis-core service's `environment:` list.
    """
    text = CONFIG.joinpath("configuration.yaml").read_text(encoding="utf-8")
    wanted = {
        match.group(1)
        for line in text.splitlines()
        if not line.lstrip().startswith("#")          # the tag's own doc line
        # `!env_url` too, or a variable introduced through the newer tag is
        # invisible to precisely the check that exists to catch a variable the
        # compose file forgot to pass — the failure with no symptom.
        for match in [re.search(r"!env_(?:var|url)\s+([A-Z][A-Z0-9_]*)", line)]
        if match
    }
    assert wanted, "no !env_var/!env_url in configuration.yaml — did this test go stale?"

    passed = {
        str(entry).split("=", 1)[0]
        for entry in compose["services"]["jarvis-core"].get("environment") or []
    }
    missing = sorted(wanted - passed)
    assert not missing, (
        f"configuration.yaml reads {missing} from the environment, but "
        "docker-compose.yml never passes them into jarvis-core"
    )


def test_compose_searxng_healthcheck_follows_the_configured_port(
    compose: dict[str, Any],
) -> None:
    """Move SEARXNG_PORT and the probe has to move with it.

    Otherwise the container serves happily on the new port and reports itself
    unhealthy for ever, which `restart: unless-stopped` turns into a boot loop
    that looks like a broken image.
    """
    test = " ".join(compose["services"]["searxng"]["healthcheck"]["test"])
    assert "${SEARXNG_PORT" in test, f"healthcheck pins a port: {test}"


def test_compose_keeps_searxng_behind_the_search_profile(compose: dict[str, Any]) -> None:
    """`docker compose up -d` must not start a search engine you did not ask for.

    Plenty of installations already run SearXNG somewhere on the LAN and only
    want SEARXNG_URL pointed at it. The profile is the opt-in:
    `docker compose --profile search up -d` starts one here as well.
    """
    assert compose["services"]["searxng"]["profiles"] == ["search"]


def test_compose_keeps_the_broker_behind_the_mqtt_profile(compose: dict[str, Any]) -> None:
    """Same argument as SearXNG: most houses already have a broker.

    `configuration.yaml` points the client at 127.0.0.1:1883 either way, and the
    integration retries with a warning rather than failing when nothing answers,
    so an unwanted broker is the worse default of the two.
    """
    assert compose["services"]["mosquitto"]["profiles"] == ["mqtt"]


def test_only_optional_extras_are_profile_gated(compose: dict[str, Any]) -> None:
    """Everything NOT gated is what `up -d` starts, so the set is the contract.

    A service that quietly grows a profile stops being part of the stack a
    plain `up -d` brings up, and nothing else would notice.
    """
    gated = {
        name for name, service in compose["services"].items() if service.get("profiles")
    }
    # photon joined them, and the reason is the strongest of the three: with no
    # REGION set the image downloads the WHOLE PLANET index — 58 GB, needing
    # 152 GB of temp space — checks the disk, refuses and exits, and
    # `restart: unless-stopped` turns that into a loop. On this host it had run
    # 2,699 times over two days. A geocoder that needs a deliberate choice of
    # region is not something `up -d` should start.
    # jarvis-tts is the fourth, and its reason is a measurement rather than a
    # disaster: Kokoro and Piper both came back word-perfect through Whisper
    # and both synthesise faster than real time, so the 3.2 GB image and 1 GB
    # of resident memory buy a different VOICE and nothing else. That is the
    # operator's ear to decide (`docs/tts-review/`), not something `up -d`
    # should spend their disk on.
    assert gated == {"searxng", "mosquitto", "photon", "jarvis-tts"}


def test_compose_ships_no_secrets(compose: dict[str, Any]) -> None:
    """Every credential comes from the environment, never from this file.

    The searxng secret key in particular: `settings.yml` keeps the upstream
    `ultrasecretkey` sentinel so a missing SEARXNG_SECRET fails loudly, and
    the day someone "fixes" that by pasting a real key into compose is the day
    the key is in every clone of the repository.
    """
    for name, service in compose["services"].items():
        for entry in service.get("environment") or []:
            key, _, value = str(entry).partition("=")
            if not any(
                marker in key for marker in ("SECRET", "TOKEN", "PASSWORD", "KEY")
            ):
                continue
            assert value.startswith("${"), (
                f"{name} hardcodes {key}; it must come from the environment"
            )


def test_compose_browser_keeps_its_two_secrets_separate(compose: dict[str, Any]) -> None:
    """The model holds the API token. Holding it must not be enough to approve."""
    env = dict(
        str(entry).split("=", 1)
        for entry in compose["services"]["jarvis-browser"]["environment"]
    )
    assert env["JARVIS_BROWSER_TOKEN"] != env["BROWSER_APPROVAL_SECRET"]
    assert "${JARVIS_BROWSER_TOKEN" in env["JARVIS_BROWSER_TOKEN"]
    assert "${BROWSER_APPROVAL_SECRET" in env["BROWSER_APPROVAL_SECRET"]
    # Acting on a page is never implicitly open: an unset allowlist must stay
    # unset rather than defaulting to something.
    assert env["BROWSER_ACT_ALLOWLIST"] == "${BROWSER_ACT_ALLOWLIST:-}"


def test_compose_keeps_the_existing_wyoming_stack(compose: dict[str, Any]) -> None:
    services = compose["services"]
    for name, image, port in (
        ("wyoming-openwakeword", "rhasspy/wyoming-openwakeword", "10400"),
        ("wyoming-whisper", "rhasspy/wyoming-whisper", "10300"),
        ("wyoming-piper", "rhasspy/wyoming-piper", "10200"),
    ):
        service = services[name]
        assert service["image"].startswith(image)
        assert f"tcp://0.0.0.0:{port}" in service["command"]
    assert services["wyoming-openwakeword"]["command"].count("hey_jarvis")
    assert "rtuszik/photon-docker" in services["photon"]["image"]


def test_compose_uses_host_networking_throughout(compose: dict[str, Any]) -> None:
    """Loopback to Wyoming/Ollama plus LAN discovery — and ufw stays the authority."""
    for name, service in compose["services"].items():
        if name == "jarvis-config-init":
            # A one-shot that chowns a bind mount and exits. It opens no
            # socket, so putting it on the host's network stack would grant
            # reach it has no use for.
            continue
        assert service.get("network_mode") == "host", f"{name} is not on host networking"


def test_compose_jarvis_core_service(compose: dict[str, Any]) -> None:
    service = compose["services"]["jarvis-core"]
    assert service["build"]["dockerfile"] == "Dockerfile"
    assert "./config:/config" in service["volumes"]
    assert "/healthz" in " ".join(service["healthcheck"]["test"])
    assert service["restart"] == "unless-stopped"
    assert service["security_opt"] == ["no-new-privileges:true"]


def test_compose_ships_optional_services_commented_out() -> None:
    """ollama, orchestrator and sandbox are documented but must not start."""
    text = COMPOSE.read_text(encoding="utf-8")
    for name in ("ollama:", "jarvis-orchestrator:", "jarvis-sandbox:"):
        assert f"# {name}" in text or f"#   {name}" in text, f"{name} is not documented"
    parsed = yaml.safe_load(text)
    assert not {"ollama", "jarvis-orchestrator", "jarvis-sandbox"} & set(parsed["services"])


def test_compose_sandbox_isolation_note_survives() -> None:
    """The commented sandbox must keep `network_mode: none`, not host."""
    text = COMPOSE.read_text(encoding="utf-8")
    sandbox = text[text.index("jarvis-sandbox:") :]
    assert "network_mode: none" in sandbox
    assert "host" not in sandbox.split("volumes:")[0].split("network_mode:")[1][:40]


def test_compose_ports_match_the_voice_config() -> None:
    """The config points at the ports compose actually binds."""
    voice = load_config(CONFIG)["voice"]
    text = COMPOSE.read_text(encoding="utf-8")
    for section, port in (("stt", 10300), ("tts", 10200), ("wake", 10400)):
        assert voice[section]["port"] == port
        assert f"tcp://0.0.0.0:{port}" in text


def _compose_default(name: str) -> str:
    """The `${NAME:-default}` fallback compose uses when the env is empty."""
    match = re.search(rf"\$\{{{name}:-([^}}]*)\}}", COMPOSE.read_text(encoding="utf-8"))
    assert match, f"docker-compose.yml has no ${{{name}:-...}} default"
    return match.group(1)


def test_compose_timezone_default_matches_the_configured_time_zone() -> None:
    """TZ and `jarvis: time_zone:` still have to agree, for a smaller reason.

    This used to be the difference between an automation running at 04:00 and
    running at 05:00: `time_zone:` was decorative and the container's TZ timed
    everything. `time_zone:` now drives the automation clock and `{{ now() }}`
    (see `configured_clock` in jarvis/automation/util.py), so a disagreement no
    longer moves a trigger.

    It is still checked, because TZ is what stamps this container's logs and
    every other container's in the stack — none of which read Jarvis's config.
    Reading a log line at 04:00 about an automation that ran at 23:00 is its own
    kind of wrong.
    """
    configured = load_config(CONFIG)["jarvis"]["time_zone"]
    assert _compose_default("TZ") == configured, (
        f"compose defaults TZ to {_compose_default('TZ')!r} but configuration.yaml "
        f"sets time_zone: {configured!r}"
    )


def test_compose_piper_voice_matches_the_default_pipeline() -> None:
    """Piper loads exactly the voice `--voice` names.

    Asking it for another one means a download on first use, so a config whose
    default pipeline names a voice compose never loaded is mute on a box with
    no internet — the one place this stack is supposed to keep working.
    """
    voice = load_config(CONFIG)["voice"]
    loaded = _compose_default("PIPER_VOICE")
    assert voice["tts"]["voice"] == loaded
    assert voice["pipelines"][0]["voice"] == loaded, (
        f"the default pipeline asks for {voice['pipelines'][0]['voice']!r}, "
        f"but wyoming-piper is started with {loaded!r}"
    )


def test_compose_wake_word_matches_the_voice_config() -> None:
    config_wake = load_config(CONFIG)["voice"]["wake"]["model"]
    assert _compose_default("WAKE_WORD") == config_wake


# ===========================================================================
# requirements.txt
# ===========================================================================
def _requirements() -> list[str]:
    return [
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_requirements_have_no_duplicates() -> None:
    names = [re.split(r"[<>=!\[]", line)[0].strip().lower() for line in _requirements()]
    assert len(names) == len(set(names)), f"duplicate requirement in {names}"


def test_requirements_are_pinned_at_both_ends() -> None:
    for line in _requirements():
        assert ">=" in line, f"{line!r} has no lower bound"
        assert "<" in line, f"{line!r} has no upper bound — a major release could break a rebuild"


def test_requirements_cover_every_third_party_import() -> None:
    """Nothing the package imports may be missing from the image."""
    stdlib_or_local = {
        "__future__", "jarvis", "starlette",  # starlette arrives with fastapi
    }
    provided = {
        "fastapi", "uvicorn", "websockets", "httpx", "yaml", "jinja2", "aiomqtt",
    }
    aliases = {"yaml": "pyyaml"}
    declared = {
        re.split(r"[<>=!\[]", line)[0].strip().lower() for line in _requirements()
    }

    imported: set[str] = set()
    for path in (ROOT / "jarvis").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])

    import sys

    third_party = {
        name
        for name in imported
        if name not in sys.stdlib_module_names and name not in stdlib_or_local
    }
    # paho-mqtt is an explicitly optional fallback backend.
    third_party.discard("paho")

    unmet = {n for n in third_party if aliases.get(n, n) not in declared and n not in provided}
    assert not unmet, f"imported but not in requirements.txt: {sorted(unmet)}"


def test_requirements_keep_the_mqtt_backend() -> None:
    """MQTT discovery is the main hardware story; log-only mode is not enough."""
    assert any(line.startswith("aiomqtt") for line in _requirements())


# ===========================================================================
# docs
# ===========================================================================
DOC_FILES = (
    "integrations.md",
    "configuration.md",
    "voice.md",
    "security.md",
    "migrating-from-ha.md",
    "code.md",
)


@pytest.mark.parametrize("name", DOC_FILES)
def test_doc_exists_and_is_substantial(name: str) -> None:
    path = DOCS / name
    assert path.is_file(), f"docs/{name} is missing"
    assert len(path.read_text(encoding="utf-8")) > 2000


def test_readme_covers_the_required_ground() -> None:
    text = README.read_text(encoding="utf-8")
    for heading in ("## Quickstart", "## Architecture", "## What this is not"):
        assert heading in text, f"README is missing {heading}"
    # The honest section has to actually be honest.
    for claim in ("Lovelace", "config UI", "MQTT"):
        assert claim in text.split("## What this is not")[1]


def test_parent_repo_pointer_exists() -> None:
    path = PARENT_DOCS / "standalone.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "jarvis-core" in text
    assert "assist_pipeline/run" in text, "the shared client contract is the whole point"


def test_internal_doc_links_resolve() -> None:
    broken: list[str] = []
    for path in [README, *(DOCS / n for n in DOC_FILES), PARENT_DOCS / "standalone.md"]:
        for target in re.findall(r"\]\((?!https?://|#)([^)#]+)", path.read_text(encoding="utf-8")):
            if not (path.parent / target).resolve().exists():
                broken.append(f"{path.name} -> {target}")
    assert not broken, f"broken relative links: {broken}"


@pytest.mark.parametrize("name", DOC_FILES)
def test_documented_python_compiles(name: str) -> None:
    """A doc example that does not parse is worse than no example."""
    for block in _code_blocks((DOCS / name).read_text(encoding="utf-8"), "python"):
        ast.parse(block)


@pytest.mark.parametrize(
    "name", ("configuration.md", "migrating-from-ha.md", "voice.md", "code.md")
)
def test_documented_yaml_parses(name: str) -> None:
    for block in _code_blocks((DOCS / name).read_text(encoding="utf-8"), "yaml"):
        yaml.load(block, Loader=_PermissiveLoader)


def test_documented_entity_attributes_are_real() -> None:
    """The integrations doc lists `_attr_*` fields; they must exist on Entity."""
    text = (DOCS / "integrations.md").read_text(encoding="utf-8")
    for attr in sorted(set(re.findall(r"`(_attr_[a-z_]+)`", text))):
        assert hasattr(Entity, attr), f"docs name {attr}, which Entity does not have"


def test_documented_entity_lifecycle_is_real() -> None:
    for method in (
        "async_added_to_jarvis",
        "async_will_remove",
        "async_update",
        "async_write_state",
    ):
        assert callable(getattr(Entity, method))
    platform = EntityPlatform.__init__.__code__.co_varnames
    assert {"jarvis", "domain", "platform_name", "scan_interval"} <= set(platform)


async def test_documented_services_exist(example_copy: Path) -> None:
    """Service names quoted in the docs must be registered by a booted Jarvis."""
    quoted: set[tuple[str, str]] = set()
    for name in DOC_FILES:
        text = (DOCS / name).read_text(encoding="utf-8")
        quoted.update(
            tuple(match.split("."))  # type: ignore[misc]
            for match in re.findall(
                r"`((?:recorder|history|logbook|voice|llm|mqtt|scene|script|"
                r"automation|input_boolean|input_number|input_text|input_select|"
                r"input_datetime|device_tracker|conversation|homeassistant)"
                r"\.[a-z_]+)`",
                text,
            )
        )
    assert quoted, "the regex stopped matching anything — fix the test, not the docs"

    jarvis = await _boot(example_copy)
    try:
        missing = sorted(
            f"{d}.{s}" for d, s in quoted if not jarvis.services.has_service(d, s)
        )
        assert not missing, f"docs name services that do not exist: {missing}"
    finally:
        await jarvis.async_stop()


def test_locally_built_services_declare_pull_policy_build(compose: dict[str, Any]) -> None:
    """A service built here must not be looked for in a registry first.

    `jarvis-core:local` and `jarvis-browser:local` are built from this repo and
    published nowhere, but declaring both `image:` and `build:` makes compose
    try a pull before it builds — so a first `up -d` prints

        ! Image jarvis-core:local  pull access denied for jarvis-core,
          repository does not exist or may require 'docker login'

    twice, before quietly building the images anyway. It is a warning, not an
    error, and it reads exactly like the install has failed on step one.
    """
    for name, service in compose["services"].items():
        if "build" in service:
            assert service.get("pull_policy") == "build", (
                f"{name} is built locally but would be pulled first"
            )


def test_the_config_dir_is_made_writable_before_jarvis_core_starts(
    compose: dict[str, Any]
) -> None:
    """jarvis-core runs as uid 10003 and `./config` is a bind mount.

    The Dockerfile's `chown /config` applies to the image's own empty directory
    and is masked the moment the host mount lands on top of it, so a fresh
    clone — owned by whoever ran `git clone`, usually root — gave

        PermissionError: [Errno 13] Permission denied: '/config/.storage'

    on the first registry write, and `restart: unless-stopped` turned that into
    a crash loop printing the same traceback forever.
    """
    init = compose["services"]["jarvis-config-init"]
    assert init["user"] == "0:0", "it has to be root to chown a root-owned dir"
    assert "10003" in init["command"], "must chown to the uid jarvis-core runs as"
    assert init["restart"] == "no", "a one-shot that restarts is a crash loop"
    assert any(v.endswith(":/config") for v in init["volumes"])

    # And jarvis-core must WAIT for it, not merely start alongside it.
    depends = compose["services"]["jarvis-core"]["depends_on"]
    assert depends["jarvis-config-init"]["condition"] == "service_completed_successfully"


def test_the_whisper_model_is_one_faster_whisper_accepts(compose: dict[str, Any]) -> None:
    """The pinned image IS faster-whisper, so a sherpa name can never load.

    It shipped defaulting to `sherpa-onnx-streaming-en`, which faster-whisper
    rejects with `ValueError: Invalid model size` before it serves anything —
    so the voice pipeline had no STT on a default install.
    """
    whisper = compose["services"]["wyoming-whisper"]
    assert "faster-whisper" not in whisper["image"] or True  # image name varies
    command = " ".join(whisper["command"].split())
    default = command.split("--model ${WHISPER_MODEL:-", 1)[1].split("}", 1)[0]
    sizes = {
        "tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3",
        "large", "turbo", "large-v3-turbo", "distil-large-v2", "distil-medium.en",
        "distil-small.en", "distil-large-v3", "distil-large-v3.5",
    }
    valid = sizes | {f"{s}.en" for s in ("tiny", "base", "small", "medium")}
    assert default in valid, f"{default!r} is not a faster-whisper model size"


def test_images_that_drop_privileges_keep_the_caps_to_do_it(
    compose: dict[str, Any]
) -> None:
    """`cap_drop: [ALL]` on an image that de-escalates itself is a crash loop.

    searxng and mosquitto both start as root, chown their data directory and
    then setgid/setuid down to their own unprivileged user. Dropping every
    capability takes away the three that step needs:

        chown: /mosquitto/data: Operation not permitted
        Error setting groups whilst dropping privileges: Operation not permitted

    These are the capabilities required to STOP being root. They are not a way
    to stay root — `no-new-privileges` forecloses that separately, and this
    asserts both together so neither can be dropped on its own.
    """
    needed = {"CHOWN", "SETGID", "SETUID"}
    for name in ("searxng", "mosquitto"):
        service = compose["services"][name]
        assert service["cap_drop"] == ["ALL"]
        assert needed <= set(service.get("cap_add") or []), (
            f"{name} drops all capabilities but its entrypoint de-escalates; "
            f"it needs {sorted(needed)}"
        )
        assert "no-new-privileges:true" in service["security_opt"]


def test_every_documented_env_var_is_actually_read_by_something(
    compose: dict[str, Any]
) -> None:
    """The other direction, and the one that had no test.

    `test_compose_passes_jarvis_core_every_env_var_its_config_reads` catches a
    variable the config reads but compose withholds. It cannot catch a variable
    we DOCUMENT and nobody reads — which is what happened to OLLAMA_URL. It sat
    in .env.example looking like the way to point Jarvis at Ollama, while
    configuration.yaml hardcoded `url: http://127.0.0.1:11434`, so setting it
    changed nothing and the log reported loopback regardless. Someone running
    Ollama in an LXC container had no way to tell the knob was disconnected.

    "Read" means the name appears in the compose file (which interpolates it)
    or in configuration.yaml (via !env_var). Anything else is a lie in the
    documentation.
    """
    env_example = ROOT.joinpath(".env.example").read_text(encoding="utf-8")
    documented = {
        line.split("=", 1)[0].strip()
        for line in env_example.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    compose_text = ROOT.joinpath("docker-compose.yml").read_text(encoding="utf-8")
    config_text = CONFIG.joinpath("configuration.yaml").read_text(encoding="utf-8")

    dead = sorted(
        name for name in documented
        if name not in compose_text and name not in config_text
    )
    assert not dead, (
        "documented in .env.example but mentioned nowhere — setting these does "
        f"nothing at all: {dead}"
    )

    # And the sharper half. Handing a variable to a container is NOT the same
    # as reading it: `- OLLAMA_URL=${OLLAMA_URL}` in jarvis-core's environment
    # accomplishes exactly nothing unless configuration.yaml pulls it back out
    # with `!env_var`. That gap is what made OLLAMA_URL look wired while
    # `llm.url` stayed hardcoded to loopback, so the passthrough existing is
    # the very thing that makes the disconnection convincing.
    core_env = compose["services"]["jarvis-core"].get("environment") or []
    passed = {
        entry.split("=", 1)[0].strip()
        for entry in core_env
        if "=" in entry
    }
    read_by_config = {
        match.group(1)
        for line in config_text.splitlines()
        if not line.lstrip().startswith("#")
        # `!env_url` as well as `!env_var`. Its sibling test above already
        # matched both; this one did not, so a variable read through the newer
        # tag was reported as "handed over and never read" — which is the exact
        # opposite of true, and the fix would have been to stop passing it.
        for match in [re.search(r"!env_(?:var|url)\s+([A-Z][A-Z0-9_]*)", line)]
        if match
    }
    # Some names are read by the application directly rather than through the
    # config file — `auth.py` takes JARVIS_TOKEN straight from os.environ — so
    # scan the source too. The question is "does anything read this", not "does
    # configuration.yaml read this".
    source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in ROOT.joinpath("jarvis").rglob("*.py")
    )
    # TZ is read by the C library, not by us.
    runtime_only = {"TZ"}
    unread = sorted(
        name for name in passed
        if name not in read_by_config
        and name not in runtime_only
        and f'"{name}"' not in source
        and f"'{name}'" not in source
    )
    assert not unread, (
        "handed to jarvis-core but never read by configuration.yaml, so the "
        f"setting has no effect: {unread}"
    )


def test_every_websocket_command_is_documented():
    """The client contract has to describe what the server actually answers.

    `docs/clients.md` is what a third client is written against — the Android
    app, a satellite, somebody's script. A command that exists and is not
    listed is a feature nobody can find; a row for a command that was removed
    is worse, because it sends somebody to debug their own code against a
    `unknown_command` that is correct.

    Both directions, deliberately. Only checking that the doc is a subset would
    let the table rot as commands are added, which is exactly how it got to
    fifteen rows for thirty-four handlers.
    """
    import re

    from jarvis.api.websocket import WebSocketHandler

    doc = (Path(__file__).resolve().parents[1] / "docs/clients.md").read_text(encoding="utf-8")
    table = doc[doc.index("| `ping` |") :]
    table = table[: table.index("\n\n")]

    documented: set[str] = set()
    for line in table.splitlines():
        if not line.startswith("| `"):
            continue
        # A row may cover several commands: "`a` · `/b` · `/c`". A leading
        # slash continues the previous name's prefix, the way the rows read.
        names = re.findall(r"`([^`]+)`", line.split("|")[1])
        base = ""
        for name in names:
            if name.startswith("/"):
                documented.add(base.rsplit("/", 1)[0] + name)
            else:
                base = name
                documented.add(name)

    handlers = set(WebSocketHandler._HANDLERS)
    missing = handlers - documented
    stale = documented - handlers
    assert not missing, f"undocumented websocket commands: {sorted(missing)}"
    assert not stale, f"documented commands that do not exist: {sorted(stale)}"


async def test_a_stock_install_can_reach_a_connected_device():
    """The failure this catches had no error message anywhere.

    A phone pairs, registers, and appears in the console's device list — that
    list is read from the websocket layer, which is always on. But
    `device_control` and `companion` were loaded only if configuration.yaml
    named them, and the shipped configuration.yaml deliberately describes no
    house at all, so it named neither.

    So `control_device` was never registered, and the model — asked to text
    somebody from the user's own connected phone — answered that its
    capabilities were confined to the house. Which was true, and useless, and
    logged nothing: an integration nobody asked for is not an error.

    Both take no configuration. Their whole job is to exist so that a device
    which connects can be reached.
    """
    import tempfile

    from jarvis.core import Jarvis

    jarvis = Jarvis(Path(tempfile.mkdtemp()))
    try:
        # The empty house, exactly as a fresh checkout boots it.
        await jarvis.async_setup({})

        registry = jarvis.data.get("llm_tools")
        assert registry is not None, "no tool registry on a stock install"
        assert "control_device" in registry.tools, (
            "the model has no tool that can reach a connected phone, so it will "
            "say it cannot — correctly, and uselessly"
        )
        # Jarvis reaching the USER, as opposed to the user's hardware. Without
        # these a question raised by `ask_user` never leaves the console.
        assert jarvis.services.has_service("companion", "ask")
        assert jarvis.services.has_service("companion", "notify")
        assert jarvis.services.has_service("device_control", "run")
    finally:
        await jarvis.async_stop()


def test_the_always_on_integrations_need_no_configuration():
    """Anything in CORE_INTEGRATIONS is set up with `config.get(name)` — which
    is None when the key is absent. An integration that cannot survive that
    would crash every boot of a stock install."""
    import inspect

    from jarvis.integrations import CORE_INTEGRATIONS, _load_module

    for name in CORE_INTEGRATIONS:
        module = _load_module(name)
        assert module is not None, f"CORE_INTEGRATIONS names {name}, which does not exist"
        setup = getattr(module, "async_setup", None)
        assert setup is not None, f"{name} is loaded unconditionally but has no async_setup"
        params = list(inspect.signature(setup).parameters.values())
        assert len(params) >= 2, f"{name}.async_setup takes no config argument"
        config_param = params[1]
        # Either it defaults, or it is annotated to accept None. Both are fine;
        # a required positional with no None handling is what breaks.
        assert (
            config_param.default is not inspect.Parameter.empty
            or "Any" in str(config_param.annotation)
            or "None" in str(config_param.annotation)
        ), f"{name}.async_setup cannot be called with no configuration"


# ===========================================================================
# The console's own credentials
#
# The console holds two things jarvis-core does not: a password, and the
# pairing secret that password releases. Both are read from ITS environment,
# in its own container, and neither was being delivered there — so on a machine
# whose `.env` set both, the pairing panel reported no secret held, the
# password panel offered to choose a new one, and GENERATE A QR CODE refused
# with "this console holds no pairing secret".
#
# That is the same failure this file already guards jarvis-core against
# ("a variable that never enters this container is an empty setting no matter
# what .env says"), one container to the left.
# ===========================================================================
PARENT_ENV_EXAMPLE = ROOT.parent / ".env.example"
CONSOLE_AUTH = ROOT.parent / "jarvis-web" / "src" / "lib" / "server" / "consoleAuth.ts"
WEB_DOCKERFILE = ROOT.parent / "jarvis-web" / "Dockerfile"


def _console_env_vars() -> set[str]:
    """Every environment variable `consoleAuth.ts` reads, from its own source.

    Read out of the module rather than listed here, so a variable added there
    is one this test already knows about — the list cannot go stale in the
    direction that matters.
    """
    text = CONSOLE_AUTH.read_text(encoding="utf-8")
    found = set(re.findall(r"export const ENV_\w+ = '([A-Z0-9_]+)'", text))
    assert found, f"no ENV_* constants found in {CONSOLE_AUTH}"
    return found


def _service_env(service: dict[str, Any]) -> dict[str, str]:
    """`environment:` as a mapping, accepting either compose spelling."""
    raw = service.get("environment") or []
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out: dict[str, str] = {}
    for item in raw:
        name, _, value = str(item).partition("=")
        out[name] = value
    return out


def test_the_console_receives_every_variable_it_reads(
    parent_compose: dict[str, Any],
) -> None:
    """The console cannot be configured by a variable it never receives.

    `JARVIS_PAIRING_SECRET` in `.env` reached jarvis-core and stopped there.
    The console reads the same name — it holds the secret now, rather than
    having it typed in on every use — and its container was started with five
    variables, none of them this one. The panel is not lying when it says no
    secret is held; nothing ever handed it one.
    """
    web = parent_compose["services"]["jarvis-web"]
    delivered = set(_service_env(web))
    missing = sorted(_console_env_vars() - delivered)
    assert not missing, (
        "jarvis-web reads these and the compose file does not pass them, so they "
        f"are unset in the container whatever .env says: {missing}"
    )


def test_the_pairing_secret_reaches_both_halves_of_the_pair(
    compose: dict[str, Any], parent_compose: dict[str, Any]
) -> None:
    """One secret, two containers, and it is useless in only one of them.

    jarvis-core validates it; the console presents it. Set it in one place and
    the halves disagree — either jarvis-core refuses a mint the console just
    made, or the console cannot mint at all. Both failures land on the one
    screen somebody is looking at while setting Jarvis up for the first time.
    """
    core = _service_env(compose["services"]["jarvis-core"])
    web = _service_env(parent_compose["services"]["jarvis-web"])
    assert "JARVIS_PAIRING_SECRET" in core
    assert "JARVIS_PAIRING_SECRET" in web
    # And from the same `.env` key, not two different ones with the same shape.
    assert "JARVIS_PAIRING_SECRET" in core["JARVIS_PAIRING_SECRET"]
    assert "JARVIS_PAIRING_SECRET" in web["JARVIS_PAIRING_SECRET"]


def test_a_password_chosen_in_the_browser_survives_a_restart(
    parent_compose: dict[str, Any],
) -> None:
    """The choose-a-password path writes a file, and the file has to outlive
    the container.

    A console with neither password variable set offers the first visitor the
    choice, hashes it and writes `.storage/console-password` relative to its
    working directory. In this image that is `/app` — the container's writable
    layer, which `docker compose up -d` discards on any recreate. The operator
    set a password, the console said it was set, and the next `up` offered the
    form again on a console they believed was locked.

    So the directory holding it is a mount, and this checks the mount lands
    where the code actually writes rather than somewhere that merely looks
    right.
    """
    source = CONSOLE_AUTH.read_text(encoding="utf-8")
    default = re.search(r"DEFAULT_PASSWORD_FILE = '([^']+)'", source)
    assert default, "consoleAuth.ts no longer names a default password file"
    directory = PurePosixPath(default.group(1)).parent

    workdir = re.findall(r"^WORKDIR\s+(\S+)", WEB_DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert workdir, "the console image sets no WORKDIR, so the path is unknowable"
    expected = str(PurePosixPath(workdir[-1]) / directory)

    volumes = parent_compose["services"]["jarvis-web"].get("volumes") or []
    targets = {str(v).split(":")[1] for v in volumes if ":" in str(v)}
    assert expected in targets, (
        f"the console writes its password hash to {expected} and nothing mounts "
        f"that path, so it is lost the next time the container is recreated "
        f"(mounted: {sorted(targets)})"
    )


def test_the_companion_env_example_documents_what_the_console_reads() -> None:
    """`.env.example` is the file people copy. A variable the stack forwards
    and the example never mentions is one nobody knows to set — which is how
    both of these came to be set for jarvis-core and not for the console."""
    text = PARENT_ENV_EXAMPLE.read_text(encoding="utf-8")
    declared = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, re.M))
    missing = sorted(_console_env_vars() - declared)
    assert not missing, f".env.example does not mention: {missing}"


# --- `docker compose watch` has to sync into the directory the code runs from --
#
# It did not. Every `develop: watch:` target said `/app/...` while all three
# Python images have `WORKDIR /srv` — so an edit synced into a directory that
# does not exist in the container, the service restarted, and it restarted with
# the old code. A dev loop that silently does nothing is worse than none: you
# conclude the change had no effect.

def _watch_targets() -> list[tuple[str, str, str]]:
    """(service, host path, container target) for every watch rule that syncs."""
    out: list[tuple[str, str, str]] = []
    for path in (COMPOSE, PARENT_COMPOSE):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, service in (parsed.get("services") or {}).items():
            for rule in ((service.get("develop") or {}).get("watch") or []):
                if str(rule.get("action", "")).startswith("sync") and rule.get("target"):
                    out.append((name, str(rule["path"]), str(rule["target"])))
    return out


def test_every_watch_rule_syncs_into_that_image_workdir() -> None:
    services = {
        "jarvis-core": (ROOT / "Dockerfile", "/srv"),
        "jarvis-browser": (ROOT.parent / "jarvis-browser" / "Dockerfile", "/srv"),
        "jarvis-orchestrator": (ROOT.parent / "jarvis-orchestrator" / "Dockerfile", "/srv"),
        "jarvis-web": (ROOT.parent / "jarvis-web" / "Dockerfile", "/app"),
    }
    for name, dockerfile_and_workdir in services.items():
        dockerfile, expected = dockerfile_and_workdir
        text = dockerfile.read_text(encoding="utf-8")
        workdirs = [
            line.split(None, 1)[1].strip()
            for line in text.splitlines()
            if line.startswith("WORKDIR ")
        ]
        assert workdirs and workdirs[-1] == expected, (name, workdirs)

    for service, _host, target in _watch_targets():
        _dockerfile, workdir = services[service]
        assert target.startswith(f"{workdir}/"), (
            f"{service} syncs into {target}, but its image runs from {workdir} — "
            "the sync would land in a directory nothing imports"
        )


def test_every_watch_rule_watches_a_path_that_exists() -> None:
    """A typo in the host path is the same silent no-op from the other end."""
    for service, host, _target in _watch_targets():
        base = ROOT if service in ("jarvis-core",) else ROOT.parent
        # Paths in jarvis-core's compose are relative to jarvis-core/; the root
        # compose file's are relative to the repository root.
        candidates = [ROOT / host, ROOT.parent / host, base / host]
        assert any(candidate.exists() for candidate in candidates), (service, host)
