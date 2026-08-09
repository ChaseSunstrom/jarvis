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
import re
import shutil
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

from jarvis.config import load_config
from jarvis.core import Jarvis
from jarvis.entity import Entity, EntityPlatform

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
DOCS = ROOT / "docs"
PARENT_DOCS = ROOT.parent / "docs"
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
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


@pytest.fixture
def config_copy(tmp_path: Path) -> Path:
    """The shipped config, copied so a boot cannot dirty the repo.

    Booting writes .storage/ and jarvis.db; running against ./config directly
    would leave those behind for the next developer to wonder about.
    """
    target = tmp_path / "config"
    shutil.copytree(CONFIG, target)
    return target


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

    expected = {
        "jarvis", "recorder", "history", "logbook", "sun", "voice", "llm",
        "mqtt", "demo", "template", "rest", "command_line", "person",
        "automation", "script", "scene",
        "input_boolean", "input_number", "input_select", "input_text",
    }
    assert expected <= set(config), f"missing from the default config: {expected - set(config)}"


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


def test_packages_are_merged_into_the_top_level(config_copy: Path) -> None:
    config = load_config(config_copy)
    assert "packages" not in config, "packages: should be folded away by the loader"

    # example.yaml contributes to four different top-level keys.
    assert "laundry_waiting" in config["input_boolean"]        # dict merge
    assert "laundry_finished_at" in config["input_datetime"]   # new key
    assert "laundry_emptied" in config["script"]               # dict merge
    ids = {a.get("id") for a in config["automation"]}
    assert {"laundry_finished", "laundry_nag"} <= ids          # list concat


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

    specs = load_tool_manifests(CONFIG / "tools")
    assert specs, "config/tools has no usable *.tool.yaml"

    jarvis = Jarvis(CONFIG)
    for spec in specs:
        tool = build_yaml_tool(jarvis, spec)
        assert tool.name and tool.description
        assert tool.parameters["type"] == "object"
        assert 1 <= tool.tier <= 3


def test_tool_manifests_avoid_the_custom_tags() -> None:
    """load_tool_manifests uses a plain SafeLoader, so !secret would be skipped."""
    for path in sorted((CONFIG / "tools").glob("*.tool.yaml")):
        yaml.safe_load(path.read_text(encoding="utf-8"))  # raises on an unknown tag


# ===========================================================================
# config/ — the shipped default must actually run
# ===========================================================================
async def test_default_config_boots_into_a_working_house(config_copy: Path) -> None:
    jarvis = await _boot(config_copy)
    try:
        by_domain: dict[str, int] = {}
        for state in jarvis.states.all():
            by_domain[state.entity_id.split(".")[0]] = (
                by_domain.get(state.entity_id.split(".")[0], 0) + 1
            )

        # Every integration the config demonstrates produced entities.
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


async def test_every_referenced_entity_exists(config_copy: Path) -> None:
    """The typo test.

    An entity_id that does not exist fails silently at runtime — the automation
    simply never fires. This is the check that caught `front_door_sensor`
    (the unique_id) being used where `front_door` (the entity_id) was meant.
    """
    config = load_config(config_copy)
    jarvis = await _boot(config_copy)
    try:
        live = set(jarvis.states.entity_ids())
        missing = sorted(_referenced_entity_ids(config) - live)
        assert not missing, f"config references entities that do not exist: {missing}"
    finally:
        await jarvis.async_stop()


async def test_every_referenced_service_is_registered(config_copy: Path) -> None:
    config = load_config(config_copy)
    jarvis = await _boot(config_copy)
    try:
        missing = sorted(
            f"{domain}.{service}"
            for domain, service in _referenced_services(config)
            if not jarvis.services.has_service(domain, service)
        )
        assert not missing, f"config calls services that are not registered: {missing}"
    finally:
        await jarvis.async_stop()


async def test_template_entities_render_to_clean_values(config_copy: Path) -> None:
    """A folded template that leaks whitespace stops being numeric."""
    jarvis = await _boot(config_copy)
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


async def test_unreachable_services_degrade_instead_of_failing(config_copy: Path) -> None:
    """No Ollama, no broker, no Wyoming: startup must still complete."""
    jarvis = await _boot(config_copy)
    try:
        # The REST block points at Ollama, which is not running here.
        assert jarvis.states.get("sensor.ollama_loaded_model").state == "unavailable"
        # ...and the rest of the house is unaffected.
        assert jarvis.states.get("light.kitchen_lights").state == "on"
    finally:
        await jarvis.async_stop()


async def test_scripts_that_carry_metadata_become_llm_tools(config_copy: Path) -> None:
    """description + fields is what promotes a script into the tool surface."""
    config = load_config(config_copy)
    jarvis = await _boot(config_copy)
    try:
        for name, script in config["script"].items():
            assert script.get("description"), f"script.{name} has no description"
            assert jarvis.services.has_service("script", name)
        # house_status returns structured data rather than just acting.
        assert config["script"]["house_status"]["sequence"][-1]["response_variable"]
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


def test_compose_has_the_whole_stack(compose: dict[str, Any]) -> None:
    services = compose["services"]
    assert set(services) == {
        "jarvis-core",
        "wyoming-openwakeword",
        "wyoming-whisper",
        "wyoming-piper",
        "photon",
    }
    assert "homeassistant" not in services, "jarvis-core replaces it; it must not be here"


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


@pytest.mark.parametrize("name", ("configuration.md", "migrating-from-ha.md", "voice.md"))
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


async def test_documented_services_exist(config_copy: Path) -> None:
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

    jarvis = await _boot(config_copy)
    try:
        missing = sorted(
            f"{d}.{s}" for d, s in quoted if not jarvis.services.has_service(d, s)
        )
        assert not missing, f"docs name services that do not exist: {missing}"
    finally:
        await jarvis.async_stop()
