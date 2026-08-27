"""Every .env variable, set from the console, kept (M114)."""

from __future__ import annotations

import re
from pathlib import Path

from jarvis.environment import (
    MASK,
    Environment,
    apply_overrides,
    is_secret,
    load_catalog,
    parse_catalog,
    read_overrides,
    write_overrides,
)

EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def test_the_catalogue_names_every_variable_env_example_does():
    text = EXAMPLE.read_text(encoding="utf-8")
    documented = set(re.findall(r"^(?:#\s*)?(?:export\s+)?([A-Z][A-Z0-9_]*)=", text, re.M))
    catalog = {v.name for v in parse_catalog(text)}
    assert documented <= catalog, sorted(documented - catalog)
    assert len(catalog) >= 30
    assert load_catalog(), "the shipped catalogue reads from beside the package"


def test_the_why_is_the_comment_above_and_secrets_are_known_by_name():
    text = "# The house's own token.\n# Long-lived.\nJARVIS_TOKEN=abc\n\n# Where the model is.\nLLM_BASE_URL=http://x/v1\n# PIPER_VOICE=en_GB-alan-medium\n"
    rows = {v.name: v for v in parse_catalog(text)}
    assert rows["JARVIS_TOKEN"].why == "The house's own token. Long-lived."
    assert rows["JARVIS_TOKEN"].secret is True
    assert rows["LLM_BASE_URL"].secret is False and rows["LLM_BASE_URL"].default == "http://x/v1"
    assert rows["PIPER_VOICE"].default == "en_GB-alan-medium", "a commented-out assignment is documented too"
    assert is_secret("SEARXNG_SECRET") and is_secret("N8N_API_KEY") and not is_secret("TZ")


def test_overrides_are_kept_and_applied_over_the_environment_at_boot(tmp_path):
    write_overrides(tmp_path, {"TZ": "Europe/London", "PIPER_VOICE": "en_GB-alan-medium"})
    assert read_overrides(tmp_path) == {"TZ": "Europe/London", "PIPER_VOICE": "en_GB-alan-medium"}
    environ = {"TZ": "America/Chicago", "OTHER": "1"}
    applied = apply_overrides(tmp_path, environ)
    assert sorted(applied) == ["PIPER_VOICE", "TZ"]
    assert environ["TZ"] == "Europe/London" and environ["_JARVIS_ENV_ORIGINAL_TZ"] == "America/Chicago"
    assert environ["PIPER_VOICE"] == "en_GB-alan-medium" and "_JARVIS_ENV_ORIGINAL_PIPER_VOICE" not in environ
    # A store that is not there is no overrides, and a bad name never reaches the environment.
    assert apply_overrides(tmp_path / "nowhere", {}) == []
    write_overrides(tmp_path, {"not a name": "x", "TZ": "UTC"})
    assert read_overrides(tmp_path) == {"TZ": "UTC"}


def test_the_console_sets_clears_and_never_lists_a_secret(tmp_path):
    env = Environment.load(tmp_path)
    assert env.catalog and env.overrides == {}
    assert env.set("JARVIS_TOKEN", "s3cr3t-value")["status"] == "ok"
    assert env.set("TZ", "Europe/Paris")["status"] == "ok"
    assert env.set("NOT_A_VARIABLE", "x")["status"] == "error"
    assert env.set("TZ", "a\nb")["status"] == "error"
    rows = {r["name"]: r for r in env.rows(environ={"TZ": "UTC"})}
    assert rows["TZ"]["value"] == "Europe/Paris" and rows["TZ"]["pending"] is True and rows["TZ"]["set"] is True
    assert rows["TZ"]["source"] == "environment" and rows["TZ"]["live"] == "UTC"
    assert rows["JARVIS_TOKEN"]["value"] == MASK and "s3cr3t" not in str(rows)
    assert env.reveal("JARVIS_TOKEN")["value"] == "s3cr3t-value"
    # Kept: a new Environment reads the same, and the row says the override is what booted.
    again = Environment.load(tmp_path)
    assert again.overrides == {"JARVIS_TOKEN": "s3cr3t-value", "TZ": "Europe/Paris"}
    rows = {r["name"]: r for r in again.rows(environ={"TZ": "Europe/Paris", "_JARVIS_ENV_ORIGINAL_TZ": "UTC"})}
    assert rows["TZ"]["pending"] is False and rows["TZ"]["source"] == "override"
    assert again.clear("TZ")["status"] == "ok" and again.clear("TZ")["status"] == "error"
    assert read_overrides(tmp_path) == {"JARVIS_TOKEN": "s3cr3t-value"}
    assert {r["name"]: r for r in again.rows(environ={})}["TZ"]["pending"] is True, "a clear applies on restart too"
