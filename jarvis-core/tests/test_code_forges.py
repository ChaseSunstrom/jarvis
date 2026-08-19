"""GitHub and GitLab, and the list of repositories Jarvis may touch.

A forge is a host, a credential, and an **allow-list**. The token can very
likely reach every repository on the account; the allow-list is what says which
ones Jarvis may. So these tests are mostly about two things:

1. **Nothing off the list is reachable**, however the path is spelled.
2. **The token does not leak** — not to the model, not into the container, not
   into the argv where `ps` would show it, and not into a URL that ends up in
   `.git/config` or an error message.

Repositories Jarvis creates in its own workspace need no entry; it made them.
Pushing one anywhere still needs a forge that permits the path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.code.forges import (  # noqa: E402
    Forge,
    ForgeError,
    askpass_script,
    check_remote_url,
    clone_url,
    forge_from_dict,
    git_env,
    is_jarvis_branch,
    local_name,
    permits,
    redact,
    split_project,
)


def forge(**kw) -> Forge:
    kw.setdefault("name", "github")
    kw.setdefault("host", "github.com")
    kw.setdefault("token", "ghp_secret")
    kw.setdefault("allow", ["chasesunstrom/jarvis"])
    return Forge(**kw)


# ---------------------------------------------------------------------------
# the allow-list
# ---------------------------------------------------------------------------
def test_a_permitted_repository_is_permitted():
    assert permits(forge(), "chasesunstrom/jarvis")


def test_anything_else_on_the_same_account_is_not():
    """The whole point: the token can reach it, and Jarvis may not."""
    assert not permits(forge(), "chasesunstrom/secrets")
    assert not permits(forge(), "someone-else/jarvis")


def test_an_empty_allow_list_permits_nothing():
    """Not everything. A forge added before anyone decided what to permit is a
    forge that does nothing, which is the safe reading of a half-written
    configuration."""
    assert not permits(forge(allow=[]), "chasesunstrom/jarvis")


def test_a_whole_account_can_be_permitted_with_a_wildcard():
    wide = forge(allow=["chasesunstrom/*"])
    assert permits(wide, "chasesunstrom/jarvis")
    assert permits(wide, "chasesunstrom/anything")
    assert not permits(wide, "someone-else/jarvis")


def test_a_wildcard_does_not_match_the_owner_alone():
    """`owner/*` is "any repository of theirs", not "them"."""
    assert not permits(forge(allow=["chasesunstrom/*"]), "chasesunstrom")


def test_matching_is_case_insensitive_because_the_forges_are():
    """An allow-list that missed `Owner/Repo` for `owner/repo` would be a rule
    that looks enforced and is not."""
    assert permits(forge(allow=["ChaseSunstrom/Jarvis"]), "chasesunstrom/jarvis")
    assert permits(forge(allow=["chasesunstrom/jarvis"]), "ChaseSunstrom/JARVIS")


def test_a_gitlab_subgroup_path_works():
    deep = forge(kind="gitlab", allow=["group/subgroup/thing"])
    assert permits(deep, "group/subgroup/thing")
    assert not permits(deep, "group/subgroup/other")


@pytest.mark.parametrize(
    "nasty",
    [
        "chasesunstrom/../../etc/passwd",
        "../jarvis",
        "/etc/passwd",
        "https://evil.test/x",
        "chasesunstrom/jarvis/../../other",
        "chasesunstrom",
        "",
        "a b/c",
        "chasesunstrom/jar\\vis",
        "ssh://git@evil/x",
    ],
)
def test_a_path_that_is_not_a_repository_path_is_refused(nasty: str):
    """This string comes from the model, and becomes a URL and a directory."""
    assert split_project(nasty) == []
    assert not permits(forge(allow=["chasesunstrom/*"]), nasty)


def test_traversal_cannot_be_smuggled_past_a_wildcard():
    wide = forge(allow=["chasesunstrom/*"])
    assert not permits(wide, "chasesunstrom/../someone-else/private")


# ---------------------------------------------------------------------------
# the credential
# ---------------------------------------------------------------------------
def test_the_clone_url_has_no_credential_in_it():
    """A URL ends up in `.git/config`, in `git remote -v`, and in every error
    git prints. A token in any of those is one nobody remembers to clean."""
    url = clone_url(forge(), "chasesunstrom/jarvis")
    assert url == "https://github.com/chasesunstrom/jarvis.git"
    assert "ghp_secret" not in url
    assert "@" not in url


def test_a_bad_project_never_becomes_a_url():
    with pytest.raises(ForgeError):
        clone_url(forge(), "../../etc/passwd")


def test_the_listing_never_carries_the_token():
    """What the console and the model are allowed to know: that it is set."""
    listed = forge().as_dict()
    assert listed["has_token"] is True
    assert "ghp_secret" not in repr(listed)
    assert "token" not in listed


def test_the_token_reaches_git_through_the_environment_not_the_argv(tmp_path: Path):
    """`/proc/*/cmdline` is world readable and `ps` is how credentials leak."""
    env = git_env(forge(), tmp_path)
    assert env["JARVIS_GIT_TOKEN"] == "ghp_secret"
    assert env["GIT_ASKPASS"].endswith("git-askpass.sh")
    # And git must not be able to stop and ask a human who is not there.
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_the_askpass_helper_is_not_readable_by_anyone_else(tmp_path: Path):
    script = askpass_script(tmp_path)
    assert script.exists()
    assert oct(script.stat().st_mode)[-3:] == "700"
    # It echoes a variable; it does not CONTAIN the secret.
    assert "ghp_secret" not in script.read_text()


def test_a_forge_with_no_token_passes_no_credential(tmp_path: Path):
    env = git_env(forge(token=""), tmp_path)
    assert "JARVIS_GIT_TOKEN" not in env
    assert "GIT_ASKPASS" not in env


def test_the_token_is_redacted_from_anything_quoted_back():
    """git puts the URL in its errors, and somebody will paste that log."""
    said = "fatal: could not read Password for 'https://ghp_secret@github.com'"
    assert "ghp_secret" not in redact(said, forge())
    assert "<token>" in redact(said, forge())


# ---------------------------------------------------------------------------
# pushing
# ---------------------------------------------------------------------------
def test_only_a_jarvis_branch_may_be_pushed():
    """Pushing `main` would put a model's work on the branch other people build
    from, with no review anywhere."""
    assert is_jarvis_branch("jarvis/20260101-abc")
    assert not is_jarvis_branch("main")
    assert not is_jarvis_branch("master")
    assert not is_jarvis_branch("")
    assert not is_jarvis_branch("feature/jarvis")


def test_a_remote_that_was_rewritten_is_refused():
    """`.git/config` is writable by a job with a container. A push follows
    whatever `origin` says, so `origin` is checked rather than trusted."""
    assert check_remote_url("https://github.com/a/b.git", forge()) == ""
    assert "not" in check_remote_url("https://evil.test/a/b.git", forge())
    assert "credential" in check_remote_url("https://u:p@github.com/a/b.git", forge())
    assert "https" in check_remote_url("ssh://git@github.com/a/b.git", forge())


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def test_a_forge_needs_a_known_kind():
    assert forge_from_dict({"name": "a", "kind": "github"}) is not None
    assert forge_from_dict({"name": "a", "kind": "gitlab"}) is not None
    assert forge_from_dict({"name": "a", "kind": "bitbucket"}) is None
    assert forge_from_dict({"name": "", "kind": "github"}) is None
    assert forge_from_dict("nonsense") is None


def test_the_default_host_follows_the_kind():
    assert forge_from_dict({"name": "a", "kind": "github"}).host == "github.com"
    assert forge_from_dict({"name": "a", "kind": "gitlab"}).host == "gitlab.com"
    assert forge_from_dict(
        {"name": "a", "kind": "gitlab", "host": "git.internal"}
    ).host == "git.internal"


def test_a_host_that_is_really_a_url_is_refused():
    """It gets concatenated into a URL; a scheme or a slash in it is a mistake
    that would point the clone somewhere else."""
    for bad in ("https://github.com", "github.com/x", "u@github.com", "gith ub.com"):
        assert forge_from_dict({"name": "a", "host": bad}) is None


def test_an_unusable_allow_entry_is_dropped_not_guessed_at():
    built = forge_from_dict(
        {"name": "a", "allow": ["good/one", "../bad", "", "also/*", "a b/c"]}
    )
    assert built.allow == ["good/one", "also/*"]


def test_a_wildcard_is_only_allowed_at_the_end():
    built = forge_from_dict({"name": "a", "allow": ["*/thing", "owner/*"]})
    assert built.allow == ["owner/*"]


def test_pushing_is_off_by_default():
    """Cloning is reading. Pushing is publishing."""
    assert forge_from_dict({"name": "a"}).push is False
    assert forge_from_dict({"name": "a", "push": True}).push is True


def test_the_local_name_is_the_last_segment():
    assert local_name("chasesunstrom/jarvis") == "jarvis"
    assert local_name("group/sub/thing") == "thing"
    assert local_name("nonsense") == ""


def test_the_description_says_how_many_are_permitted_and_whether_it_can_push():
    assert "1 repository permitted" in forge().describe()
    assert "read-only" in forge().describe()
    assert "read-only" not in forge(push=True).describe()


def test_the_console_and_the_server_agree_about_the_allow_list():
    """One table, two implementations.

    `permits()` here decides whether a clone happens. `whyNotProject` in
    `jarvis-web/src/lib/code.ts` copies the rule so the console can refuse
    before a round trip — the copy is for the message, never for the decision.
    A copy that DRIFTS is the worst of both: the form accepts what the server
    rejects, with a different sentence, and the reader blames the form.

    So both suites read `tests/contracts/forge_allow_list.json` and neither
    owns the answers. A case added on one side and not handled on the other
    fails there, which is the point.
    """
    import json

    table = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "contracts"
        / "forge_allow_list.json"
    )
    assert table.is_file(), f"the shared allow-list table is missing: {table}"
    cases = json.loads(table.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 15, "the shared table lost most of its cases"

    for case in cases:
        forge = Forge(name="f", kind="github", host="github.com", allow=list(case["allow"]))
        assert permits(forge, case["project"]) is case["permitted"], (
            f"allow={case['allow']} project={case['project']!r}: "
            f"server said {permits(forge, case['project'])}, "
            f"table says {case['permitted']}"
        )
