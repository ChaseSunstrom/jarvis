import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_config as gc  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parents[1]


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_paperless_example_generates(tmp_path):
    manifests = [TOOLS_DIR / "paperless_search.tool.yaml"]
    secrets = write(tmp_path, "secrets.yaml", "paperless_token: abc123\n")
    summary = gc.generate(manifests, tmp_path / "out", secrets)
    assert summary["generated"] == ["paperless_search"]
    assert summary["expose"] == ["script.paperless_search"]

    out = (tmp_path / "out" / "jarvis_tools.yaml").read_text()
    # !secret must survive as a real YAML tag (full-value; HA cannot splice
    # secrets mid-string, so 'Token !secret x' normalises to '!secret x'
    # with a warning telling the user the secret must hold the full value)
    assert (
        "Authorization: !secret paperless_token" in out
        or "Authorization: !secret 'paperless_token'" in out
    )
    assert any("full" in w.lower() for w in summary["warnings"])
    # no anchors/aliases — HA chokes less and diffs stay readable
    assert "&id" not in out and "*id" not in out


def test_generated_yaml_is_valid_ha_shape(tmp_path):
    secrets = write(tmp_path, "secrets.yaml", "paperless_token: abc123\n")
    gc.generate([TOOLS_DIR / "paperless_search.tool.yaml"], tmp_path / "o", secrets)
    text = (tmp_path / "o" / "jarvis_tools.yaml").read_text()

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor("!secret", lambda ldr, node: f"SECRET({node.value})")
    doc = yaml.load(text, Loader=Loader)
    assert "rest_command" in doc and "script" in doc
    rc = doc["rest_command"]["jarvis_tool_paperless_search"]
    assert rc["method"] == "get"
    assert "{{ query }}" in rc["url"]
    sc = doc["script"]["paperless_search"]
    assert sc["fields"]["query"]["required"] is True
    # script must return the rest response to the LLM
    assert any("stop" in step for step in sc["sequence"])


def test_missing_secret_disables_tool(tmp_path):
    secrets = write(tmp_path, "secrets.yaml", "unrelated: x\n")
    summary = gc.generate(
        [TOOLS_DIR / "paperless_search.tool.yaml"], tmp_path / "o", secrets
    )
    assert summary["generated"] == []
    assert summary["disabled"][0][0] == "paperless_search"
    text = (tmp_path / "o" / "jarvis_tools.yaml").read_text()
    assert "DISABLED" in text and "paperless_token" in text
    # the disabled block must be fully commented → YAML still loads
    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor("!secret", lambda ldr, node: None)
    yaml.load(text, Loader=Loader)


def test_no_secrets_file_means_optimistic(tmp_path):
    summary = gc.generate(
        [TOOLS_DIR / "paperless_search.tool.yaml"], tmp_path / "o", None
    )
    assert summary["generated"] == ["paperless_search"]


def test_tier3_gets_approval_gate(tmp_path):
    m = write(
        tmp_path,
        "danger.tool.yaml",
        """
name: garage_door_api
description: "Open the garage via vendor API"
tier: 3
service:
  method: POST
  url: "http://192.168.2.50/open"
  fields:
    side: { description: "which door", required: true }
""",
    )
    summary = gc.generate([m], tmp_path / "o", None)
    assert summary["generated"] == ["garage_door_api"]
    text = (tmp_path / "o" / "jarvis_tools.yaml").read_text()

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor("!secret", lambda ldr, node: None)
    doc = yaml.load(text, Loader=Loader)
    seq = doc["script"]["garage_door_api"]["sequence"]
    # first step must be the approval gate, before any rest_command action
    assert seq[0]["action"] == "script.jarvis_request_approval"
    rest_idx = next(
        i for i, s in enumerate(seq) if s.get("action", "").startswith("rest_command.")
    )
    gate_idx = next(i for i, s in enumerate(seq) if "if" in s)
    assert gate_idx < rest_idx


def test_bad_name_rejected(tmp_path):
    m = write(
        tmp_path,
        "bad.tool.yaml",
        "name: 'Bad Name!'\ndescription: x\nservice: {url: 'http://x'}\n",
    )
    with pytest.raises(gc.ManifestError):
        gc.generate([m], tmp_path / "o", None)


def test_bad_tier_rejected(tmp_path):
    m = write(
        tmp_path,
        "bad2.tool.yaml",
        "name: okay_tool\ndescription: x\ntier: 9\nservice: {url: 'http://x'}\n",
    )
    with pytest.raises(gc.ManifestError):
        gc.generate([m], tmp_path / "o", None)
