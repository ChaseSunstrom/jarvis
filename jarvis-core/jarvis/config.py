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


def merge_packages(config: dict[str, Any]) -> dict[str, Any]:
    """Fold `packages:` into the top level.

    Lists concatenate (automations, sensors...), dicts merge shallowly, and
    conflicting scalars are an error you actually want to see.
    """
    packages = config.pop("packages", None) or {}
    for pkg_name, package in packages.items():
        if not isinstance(package, dict):
            raise ConfigError(f"package {pkg_name!r} must be a mapping")
        for key, value in package.items():
            if key not in config or config[key] is None:
                config[key] = value
            elif isinstance(config[key], list) and isinstance(value, list):
                config[key] = config[key] + value
            elif isinstance(config[key], dict) and isinstance(value, dict):
                overlap = set(config[key]) & set(value)
                if overlap:
                    raise ConfigError(
                        f"package {pkg_name!r} redefines {key}: {sorted(overlap)}"
                    )
                config[key] = {**config[key], **value}
            else:
                raise ConfigError(
                    f"package {pkg_name!r} conflicts with existing config key {key!r}"
                )
    return config


def load_config(config_dir: str | Path) -> dict[str, Any]:
    """Load <config_dir>/configuration.yaml with includes/secrets/packages."""
    config_dir = Path(config_dir).resolve()
    secrets = load_secrets(config_dir)
    config = load_yaml(config_dir / "configuration.yaml", config_dir, secrets)
    if not isinstance(config, dict):
        raise ConfigError("configuration.yaml must be a mapping at the top level")
    return merge_packages(config)
