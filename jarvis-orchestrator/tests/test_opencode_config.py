"""OpenCode 1.x runs against the house's model server only if it is told
about it: a provider in its own config, and `provider/model` names."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.opencode import HOUSE_PROVIDER, build_command, write_opencode_config  # noqa: E402


def test_a_bare_model_name_is_the_house_provider_and_a_named_one_is_kept():
    assert build_command("house-model", "fix it")[:4] == ["opencode", "run", "--model", f"{HOUSE_PROVIDER}/house-model"]
    assert build_command("anthropic/claude", "fix it")[3] == "anthropic/claude"


def test_the_config_names_the_server_the_key_and_the_models(tmp_path):
    path = write_opencode_config(tmp_path / "x" / "opencode.json", "http://gateway:4000/v1/", "k-1", ["coder", "planner", "coder"])
    cfg = json.loads(path.read_text())
    house = cfg["provider"][HOUSE_PROVIDER]
    assert house["npm"] == "@ai-sdk/openai-compatible"
    assert house["options"] == {"baseURL": "http://gateway:4000/v1", "apiKey": "k-1"}
    assert list(house["models"]) == ["coder", "planner"]


def test_no_key_means_no_key_in_the_file(tmp_path):
    cfg = json.loads(write_opencode_config(tmp_path / "opencode.json", "http://g/v1", "", ["m"]).read_text())
    assert "apiKey" not in cfg["provider"][HOUSE_PROVIDER]["options"]
