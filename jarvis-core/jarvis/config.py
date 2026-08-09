"""YAML configuration: !secret, !include*, !env_var, and package merging.

Deliberately HA-shaped so existing muscle memory (and much existing YAML)
carries over:

    # configuration.yaml
    jarvis:
      name: Jarvis
      latitude: 40.0
    packages: !include_dir_named packages
    mqtt: !include mqtt.yaml
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_LOGGER = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


class JarvisSafeLoader(yaml.SafeLoader):
    """SafeLoader with the config-dir bound for relative includes."""

    config_dir: Path = Path(".")
    secrets: dict[str, Any] = {}


def _rel(loader: JarvisSafeLoader, node: yaml.Node) -> Path:
    return loader.config_dir / str(loader.construct_scalar(node))  # type: ignore[arg-type]


def _secret(loader: JarvisSafeLoader, node: yaml.Node) -> Any:
    key = str(loader.construct_scalar(node))  # type: ignore[arg-type]
    if key not in loader.secrets:
        raise ConfigError(
            f"secret {key!r} not found in secrets.yaml (referenced in your config)"
        )
    return loader.secrets[key]


def _env_var(loader: JarvisSafeLoader, node: yaml.Node) -> Any:
    args = str(loader.construct_scalar(node)).split()  # type: ignore[arg-type]
    name = args[0]
    default = args[1] if len(args) > 1 else None
    value = os.environ.get(name, default)
    if value is None:
        raise ConfigError(f"environment variable {name} is not set and has no default")
    return value


def _include(loader: JarvisSafeLoader, node: yaml.Node) -> Any:
    return load_yaml(_rel(loader, node), loader.config_dir, loader.secrets)


def _include_dir_named(loader: JarvisSafeLoader, node: yaml.Node) -> dict[str, Any]:
    root = _rel(loader, node)
    out: dict[str, Any] = {}
    for path in sorted(root.glob("*.yaml")):
        out[path.stem] = load_yaml(path, loader.config_dir, loader.secrets)
    return out


def _include_dir_merge_named(loader: JarvisSafeLoader, node: yaml.Node) -> dict[str, Any]:
    root = _rel(loader, node)
    out: dict[str, Any] = {}
    for path in sorted(root.glob("*.yaml")):
        loaded = load_yaml(path, loader.config_dir, loader.secrets)
        if isinstance(loaded, dict):
            out.update(loaded)
    return out


def _include_dir_list(loader: JarvisSafeLoader, node: yaml.Node) -> list[Any]:
    root = _rel(loader, node)
    return [load_yaml(p, loader.config_dir, loader.secrets) for p in sorted(root.glob("*.yaml"))]


def _include_dir_merge_list(loader: JarvisSafeLoader, node: yaml.Node) -> list[Any]:
    out: list[Any] = []
    for item in _include_dir_list(loader, node):
        if isinstance(item, list):
            out.extend(item)
        elif item is not None:
            out.append(item)
    return out


for tag, fn in (
    ("!secret", _secret),
    ("!env_var", _env_var),
    ("!include", _include),
    ("!include_dir_named", _include_dir_named),
    ("!include_dir_merge_named", _include_dir_merge_named),
    ("!include_dir_list", _include_dir_list),
    ("!include_dir_merge_list", _include_dir_merge_list),
):
    JarvisSafeLoader.add_constructor(tag, fn)


def load_yaml(path: Path, config_dir: Path, secrets: dict[str, Any]) -> Any:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    class _Loader(JarvisSafeLoader):
        pass

    _Loader.config_dir = config_dir
    _Loader.secrets = secrets
    with path.open("r", encoding="utf-8") as handle:
        return yaml.load(handle, Loader=_Loader) or {}


def load_secrets(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "secrets.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


# ---------------------------------------------------------------------------
# tag provenance
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Tagged:
    """A value that came from a tag, described without being resolved.

    Carries names and shapes only — an environment variable's name and whether
    it is set, a secret's key — and never a value. The point is that the
    settings console can explain *why* a value is what it is ("this comes from
    $OLLAMA_MODEL, which is not set, so the YAML default is being used") to
    someone who has just watched their .env be ignored, without that
    explanation becoming a way to read secrets.yaml over HTTP.
    """

    tag: str
    env_var: str | None = None
    env_set: bool = False
    yaml_default: Any = None
    secret_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "env_var": self.env_var,
            "env_set": self.env_set,
            "yaml_default": self.yaml_default,
            "secret_key": self.secret_key,
        }


class _ProvLoader(JarvisSafeLoader):
    """Parses the same YAML, resolving nothing.

    `secrets` is pinned empty rather than merely unused: if an include path is
    ever missed and a node reaches the inherited `!secret` constructor, an
    empty map makes it raise instead of returning the secret. Fail closed by
    construction, not by remembering to be careful.
    """

    secrets: dict[str, Any] = {}


def _prov_env_var(loader: "_ProvLoader", node: yaml.Node) -> Tagged:
    args = str(loader.construct_scalar(node)).split()  # type: ignore[arg-type]
    name = args[0]
    default = args[1] if len(args) > 1 else None
    return Tagged(
        tag="env_var",
        env_var=name,
        env_set=os.environ.get(name) is not None,
        yaml_default=default,
    )


def _prov_secret(loader: "_ProvLoader", node: yaml.Node) -> Tagged:
    # The key's *name*. secrets.yaml is never opened on this path.
    return Tagged(tag="secret", secret_key=str(loader.construct_scalar(node)))  # type: ignore[arg-type]


def _prov_load_yaml(path: Path, config_dir: Path) -> Any:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    class _L(_ProvLoader):
        pass

    _L.config_dir = config_dir
    with path.open("r", encoding="utf-8") as handle:
        return yaml.load(handle, Loader=_L) or {}


def _prov_include(loader: "_ProvLoader", node: yaml.Node) -> Any:
    return _prov_load_yaml(_rel(loader, node), loader.config_dir)


def _prov_include_dir_named(loader: "_ProvLoader", node: yaml.Node) -> dict[str, Any]:
    directory = _rel(loader, node)
    return {
        path.stem: _prov_load_yaml(path, loader.config_dir)
        for path in sorted(directory.glob("*.yaml"))
    }


def _prov_include_dir_merge_named(loader: "_ProvLoader", node: yaml.Node) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in sorted(_rel(loader, node).glob("*.yaml")):
        loaded = _prov_load_yaml(path, loader.config_dir)
        if isinstance(loaded, dict):
            merged.update(loaded)
    return merged


def _prov_include_dir_list(loader: "_ProvLoader", node: yaml.Node) -> list[Any]:
    return [
        _prov_load_yaml(path, loader.config_dir)
        for path in sorted(_rel(loader, node).glob("*.yaml"))
    ]


def _prov_include_dir_merge_list(loader: "_ProvLoader", node: yaml.Node) -> list[Any]:
    merged: list[Any] = []
    for path in sorted(_rel(loader, node).glob("*.yaml")):
        loaded = _prov_load_yaml(path, loader.config_dir)
        if isinstance(loaded, list):
            merged.extend(loaded)
    return merged


# The include constructors have to be re-registered on _ProvLoader rather than
# inherited. The inherited ones call `load_yaml`, which builds its loader from
# `JarvisSafeLoader` — a *sibling* of this class, not a descendant — so an
# included file would be parsed by the ordinary loader, resolving every
# `!secret` in it and losing every marker. The shipped configuration reaches
# automations.yaml, scripts.yaml, scenes.yaml and the whole packages/ directory
# through exactly those tags, which is to say: through all of them.
for _tag, _fn in (
    ("!env_var", _prov_env_var),
    ("!secret", _prov_secret),
    ("!include", _prov_include),
    ("!include_dir_named", _prov_include_dir_named),
    ("!include_dir_merge_named", _prov_include_dir_merge_named),
    ("!include_dir_list", _prov_include_dir_list),
    ("!include_dir_merge_list", _prov_include_dir_merge_list),
):
    _ProvLoader.add_constructor(_tag, _fn)


def _collect_tagged(value: Any, path: str, into: dict[str, dict[str, Any]]) -> None:
    if isinstance(value, Tagged):
        into[path] = value.as_dict()
    elif isinstance(value, dict):
        for key, child in value.items():
            _collect_tagged(child, f"{path}.{key}" if path else str(key), into)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _collect_tagged(child, f"{path}.{index}", into)


def load_provenance(config_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Where each tagged value in the configuration came from.

    A second parse whose only product is the map; the tree it builds is thrown
    away. Keys are dotted paths into the merged configuration
    (``llm.model``), values describe the tag.

    This exists for one screen. A user who sets OLLAMA_MODEL in their .env and
    finds Jarvis still loading the old model has no way to discover that
    configuration.yaml names a different variable, or that the variable is
    read but unset — they can only conclude the setting does not work. The
    console can say so, in the row for that setting, if it is told.
    """
    config_dir = Path(config_dir).resolve()
    parsed = _prov_load_yaml(config_dir / "configuration.yaml", config_dir)
    if not isinstance(parsed, dict):
        raise ConfigError("configuration.yaml must be a mapping at the top level")

    # Fold packages the same way the real loader does, so the paths line up
    # with the config the rest of the system sees. Conflicts are not this
    # function's business — load_config will raise about them soon enough, and
    # a provenance lookup should not be the thing that reports it.
    packages = parsed.pop("packages", None) or {}
    for package in packages.values():
        if not isinstance(package, dict):
            continue
        for key, value in package.items():
            if key not in parsed or parsed[key] is None:
                parsed[key] = value
            elif isinstance(parsed[key], dict) and isinstance(value, dict):
                parsed[key] = {**value, **parsed[key]}

    found: dict[str, dict[str, Any]] = {}
    _collect_tagged(parsed, "", found)
    return found


def merge_packages(
    config: dict[str, Any], provenance: dict[str, str] | None = None
) -> dict[str, Any]:
    """Fold `packages:` into the top level.

    Lists concatenate (automations, sensors...), dicts merge shallowly, and
    conflicting scalars are an error you actually want to see.

    `provenance`, when given, is filled in with `key -> package name` for
    everything a package supplied. This is the only moment that information
    exists: once merged, a value a package contributed is structurally
    identical to one written in configuration.yaml, and nothing downstream can
    tell them apart. The settings overlay needs to, so it can refuse to shadow
    a file the user edits under `packages/` rather than silently winning over
    it, and so the console can name the file it would otherwise have hidden.

    Dict merges record one entry per subkey (`llm.model` rather than `llm`),
    because that branch is exactly where a package supplies one key of a
    mapping and configuration.yaml supplies another. Concatenated lists record
    the top-level key only: a merged list genuinely has no per-item origin.
    """
    packages = config.pop("packages", None) or {}
    for pkg_name, package in packages.items():
        if not isinstance(package, dict):
            raise ConfigError(f"package {pkg_name!r} must be a mapping")
        for key, value in package.items():
            if key not in config or config[key] is None:
                config[key] = value
                if provenance is not None:
                    provenance[key] = pkg_name
            elif isinstance(config[key], list) and isinstance(value, list):
                config[key] = config[key] + value
                if provenance is not None:
                    provenance[key] = pkg_name
            elif isinstance(config[key], dict) and isinstance(value, dict):
                overlap = set(config[key]) & set(value)
                if overlap:
                    raise ConfigError(
                        f"package {pkg_name!r} redefines {key}: {sorted(overlap)}"
                    )
                config[key] = {**config[key], **value}
                if provenance is not None:
                    for subkey in value:
                        provenance[f"{key}.{subkey}"] = pkg_name
            else:
                raise ConfigError(
                    f"package {pkg_name!r} conflicts with existing config key {key!r}"
                )
    return config


def load_config(config_dir: str | Path) -> dict[str, Any]:
    """Load <config_dir>/configuration.yaml with includes/secrets/packages."""
    return load_config_with_provenance(config_dir)[0]


def load_config_with_provenance(
    config_dir: str | Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    """:func:`load_config`, plus a map of which package supplied which key.

    Separate entry point rather than a changed return type, so that every
    existing caller — and there are many — keeps working unchanged and the
    provenance path is a pure addition rather than a migration.
    """
    config_dir = Path(config_dir).resolve()
    secrets = load_secrets(config_dir)
    config = load_yaml(config_dir / "configuration.yaml", config_dir, secrets)
    if not isinstance(config, dict):
        raise ConfigError("configuration.yaml must be a mapping at the top level")
    provenance: dict[str, str] = {}
    return merge_packages(config, provenance), provenance
