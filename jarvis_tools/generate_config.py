#!/usr/bin/env python3
"""Turn jarvis_tools/*.tool.yaml manifests into Home Assistant config.

Drop a small manifest next to this script, run it, restart HA. Each manifest
becomes:

  * a ``rest_command.jarvis_tool_<name>`` performing the HTTP call
  * a ``script.<name>`` with matching typed fields (this is what the LLM sees;
    HA exposes scripts to Assist as tools)
  * an entry in the generated expose list (``jarvis_expose.yaml``) consumed by
    scripts/expose-tools.py or applied by hand in the Assist UI

Manifest format (everything under ``service`` mirrors rest_command):

    name: paperless_search
    description: "Search Paperless-ngx documents by query text"
    tier: 1                      # 1 = free, 2 = background-capable, 3 = gated
    service:
      method: GET
      url: "http://192.168.2.175:8000/api/documents/?query={{ query }}"
      headers: { Authorization: "Token !secret paperless_token" }
      fields:
        query: { description: "search text", required: true }

Tier 3 manifests are wrapped in the human-approval gate: the generated script
calls script.jarvis_request_approval first and aborts unless it returns
``approved``. The gate lives in ha-config/packages/jarvis/jarvis_orchestrator.yaml
and is enforced OUTSIDE the model — persona/prompt content can never bypass it.

If a manifest references ``!secret foo`` and ``foo`` is missing from the
secrets file (``--secrets``), the whole tool block is emitted commented-out
with a note, so a restart never fails on a half-configured tool.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent / "ha-config" / "generated"

VALID_NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
SECRET_RE = re.compile(r"!secret\s+([A-Za-z0-9_]+)")


class ManifestError(ValueError):
    pass


def load_manifest(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ManifestError(f"{path.name}: manifest must be a mapping")
    for key in ("name", "description", "service"):
        if key not in data:
            raise ManifestError(f"{path.name}: missing required key '{key}'")
    name = data["name"]
    if not VALID_NAME.match(name):
        raise ManifestError(
            f"{path.name}: name '{name}' must match {VALID_NAME.pattern}"
        )
    svc = data["service"]
    if "url" not in svc:
        raise ManifestError(f"{path.name}: service.url is required")
    tier = int(data.get("tier", 1))
    if tier not in (1, 2, 3):
        raise ManifestError(f"{path.name}: tier must be 1, 2 or 3")
    data["tier"] = tier
    svc.setdefault("method", "GET")
    svc.setdefault("fields", {})
    return data


def secrets_referenced(manifest: dict) -> set[str]:
    return set(SECRET_RE.findall(yaml.safe_dump(manifest)))


def load_available_secrets(secrets_path: Path | None) -> set[str] | None:
    """Return known secret names, or None when no secrets file was given
    (in which case we optimistically assume all secrets exist)."""
    if secrets_path is None:
        return None
    if not secrets_path.exists():
        return set()
    loaded = yaml.safe_load(secrets_path.read_text()) or {}
    return set(loaded.keys()) if isinstance(loaded, dict) else set()


class SecretTag(str):
    """Marker so we can round-trip `!secret name` through PyYAML."""


def _secret_representer(dumper, value):
    return dumper.represent_scalar("!secret", str(value))


yaml.SafeDumper.add_representer(SecretTag, _secret_representer)


def _reify_secrets(obj, warnings: list[str] | None = None):
    """Replace '!secret x' strings with real !secret tags on dump.

    HA only allows !secret as the ENTIRE value — 'Token !secret x' is not
    valid HA YAML. When a manifest embeds a secret mid-string we normalise to
    a full-value secret and warn: the secret itself must then contain the
    full string (e.g. paperless_token: "Token abc123...").
    """
    if isinstance(obj, dict):
        return {k: _reify_secrets(v, warnings) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_reify_secrets(v, warnings) for v in obj]
    if isinstance(obj, str):
        m = SECRET_RE.search(obj)
        if m:
            prefix = obj[: m.start()].strip()
            if prefix and warnings is not None:
                warnings.append(
                    f"secret '{m.group(1)}' used with prefix '{prefix}': "
                    f"HA cannot splice secrets into strings — put the FULL "
                    f"value (including '{prefix} ') inside the secret"
                )
            return SecretTag(m.group(1))
    return obj


def build_rest_command(manifest: dict, warnings: list[str] | None = None) -> dict:
    svc = manifest["service"]
    cmd: dict = {
        "url": svc["url"],
        "method": svc["method"].lower(),
        "timeout": svc.get("timeout", 20),
    }
    if svc.get("headers"):
        cmd["headers"] = svc["headers"]
    if svc.get("payload") is not None:
        cmd["payload"] = svc["payload"]
    if svc.get("content_type"):
        cmd["content_type"] = svc["content_type"]
    return {f"jarvis_tool_{manifest['name']}": _reify_secrets(cmd, warnings)}


def build_script(manifest: dict) -> dict:
    name = manifest["name"]
    fields = {}
    for fname, spec in manifest["service"]["fields"].items():
        spec = dict(spec or {})
        fields[fname] = {
            "description": spec.get("description", fname),
            "required": bool(spec.get("required", False)),
            "selector": spec.get("selector", {"text": None}),
        }
        if "example" in spec:
            fields[fname]["example"] = spec["example"]

    sequence: list[dict] = []
    if manifest["tier"] == 3:
        # Human approval gate — enforced here, outside the model. The gate
        # script returns {"approved": true|false}; anything but true aborts.
        sequence += [
            {
                "action": "script.jarvis_request_approval",
                "data": {
                    "summary": (
                        f"Tool '{name}': "
                        "{{ dict(**(field_args | default({}))) | to_json }}"
                    ),
                },
                "response_variable": "gate",
            },
            {
                "if": [
                    {
                        "condition": "template",
                        "value_template": "{{ not gate.approved }}",
                    }
                ],
                "then": [
                    {
                        "stop": "Denied by user — nothing was executed.",
                        "response_variable": "gate",
                    }
                ],
            },
        ]

    # NOTE: build two separate dicts so PyYAML never emits anchors/aliases.
    sequence += [
        {
            "variables": {
                "field_args": {
                    fname: f"{{{{ {fname} | default('') }}}}" for fname in fields
                }
            }
        },
        {
            "action": f"rest_command.jarvis_tool_{name}",
            "data": {
                fname: f"{{{{ {fname} | default('') }}}}" for fname in fields
            },
            "response_variable": "resp",
        },
        {"stop": "done", "response_variable": "resp"},
    ]

    return {
        name: {
            "alias": f"Jarvis tool: {name}",
            "description": manifest["description"],
            "mode": "parallel",
            "max": 5,
            "fields": fields,
            "sequence": sequence,
        }
    }


def comment_block(text: str, reason: str) -> str:
    body = "\n".join(f"# {line}" if line else "#" for line in text.splitlines())
    return f"# DISABLED — {reason}\n{body}\n"


def generate(
    manifest_paths: list[Path],
    out_dir: Path,
    secrets_path: Path | None = None,
) -> dict:
    """Generate config; returns a summary dict (used by tests and CLI)."""
    available = load_available_secrets(secrets_path)
    rest_commands: dict = {}
    scripts: dict = {}
    disabled: list[tuple[str, str]] = []
    expose: list[str] = []
    warnings: list[str] = []

    for path in sorted(manifest_paths):
        manifest = load_manifest(path)
        name = manifest["name"]
        missing = (
            {s for s in secrets_referenced(manifest) if s not in available}
            if available is not None
            else set()
        )
        if missing:
            disabled.append(
                (name, f"missing secret(s): {', '.join(sorted(missing))}")
            )
            continue
        rest_commands.update(build_rest_command(manifest, warnings))
        scripts.update(build_script(manifest))
        expose.append(f"script.{name}")

    out_dir.mkdir(parents=True, exist_ok=True)
    header = (
        "# GENERATED by jarvis_tools/generate_config.py — do not edit.\n"
        "# Re-run the generator after changing any *.tool.yaml manifest.\n"
    )
    doc = {"rest_command": rest_commands, "script": scripts}
    text = header + yaml.safe_dump(doc, sort_keys=True, width=100)
    for name, reason in disabled:
        text += "\n" + comment_block(f"tool: {name}", reason)
    (out_dir / "jarvis_tools.yaml").write_text(text)

    (out_dir / "jarvis_expose.yaml").write_text(
        header
        + yaml.safe_dump({"expose_to_assist": expose}, sort_keys=True)
    )

    return {
        "generated": sorted(scripts.keys()),
        "disabled": disabled,
        "expose": expose,
        "warnings": warnings,
        "out": str(out_dir / "jarvis_tools.yaml"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tools-dir", type=Path, default=HERE, help="dir with *.tool.yaml"
    )
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--secrets",
        type=Path,
        default=None,
        help="HA secrets.yaml — tools with missing secrets are commented out",
    )
    args = ap.parse_args(argv)

    manifests = sorted(args.tools_dir.glob("*.tool.yaml"))
    if not manifests:
        print(f"no *.tool.yaml manifests in {args.tools_dir}", file=sys.stderr)
        return 1
    try:
        summary = generate(manifests, args.out_dir, args.secrets)
    except ManifestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"wrote {summary['out']}")
    for name in summary["generated"]:
        print(f"  + script.{name}")
    for name, reason in summary["disabled"]:
        print(f"  - {name} (disabled: {reason})")
    for warning in summary["warnings"]:
        print(f"  ! {warning}")
    print("restart Home Assistant (or reload scripts + rest_command) to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
