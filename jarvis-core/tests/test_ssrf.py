"""Where a tool the model wrote is allowed to point.

## Why this exists

`create_tool` is Tier 3 and a human reads the manifest, and for the whole life
of the feature the only thing checked about its url was that it began with
`http://` or `https://`. So an approved tool could name jarvis-core's own API on
loopback — reaching around every gate in `llm/tools.py` by asking the server to
do it — or `169.254.169.254`, the cloud metadata service, and read the
instance's credentials out of it. A person at an approval prompt reads a
sentence; they do not resolve a host, and `http://2130706433/` is a question
nobody should be asked to answer by eye.

## The two halves this pins

1. **The threat is blocked**, in every spelling libc honours.
2. **The LAN is not.** This is a house assistant: a tool pointing at a NAS, a
   printer or `http://192.168.1.5/bins` is the ordinary case and the repo's own
   worked example. A guard that blocked RFC1918 would be one nobody could ship
   with, so the policy here is deliberately narrower than
   `jarvis-desktop`'s — whose `http_request` fetches public pages and should
   block the LAN. The parsers are shared; the policy is not, and the last test
   here is what keeps that distinction honest rather than accidental.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.helpers import ssrf  # noqa: E402
from jarvis.llm.authored_tools import AuthoredToolError, validate  # noqa: E402


def _manifest(url: str) -> dict:
    return {"name": "some_tool", "description": "d", "service": {"url": url}}


def _as_the_model(url: str) -> dict:
    """Validate the way `create_tool` does — the model wrote this url."""
    return validate(_manifest(url), allow_local_targets=False)


def _as_the_console(url: str) -> dict:
    """Validate the way the console does — the operator wrote this url."""
    return validate(_manifest(url))


# ---------------------------------------------------------------------------
# blocked
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        # jarvis-core's own API, the orchestrator, the browser service.
        "http://127.0.0.1:8080/api/services/lock/unlock",
        "http://127.1/x",
        "https://localhost/x",
        "http://LOCALHOST/x",
        "http://foo.localhost/x",
        "http://[::1]:8080/x",
        # The same loopback in every spelling `inet_aton` accepts. Each of
        # these is a real address to the resolver and a puzzle to a human.
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://0x7f.0.0.1/",
        "http://[::ffff:127.0.0.1]/",
        # Cloud metadata: credentials, one GET away.
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://instance-data/latest/",
        "http://[fd00:ec2::254]/latest/",
        # Unroutable oddities with no legitimate use from a tool.
        "http://0.0.0.0:8080/",
        "http://255.255.255.255/",
        "http://224.0.0.1/",
    ],
)
def test_a_tool_the_model_wrote_cannot_point_at_the_machine_itself(url: str) -> None:
    with pytest.raises(AuthoredToolError) as err:
        _as_the_model(url)
    assert "cannot be reached" in str(err.value)


def test_credentials_in_the_url_are_refused() -> None:
    """`http://user:pass@host/` is how a url smuggles an authorization header."""
    with pytest.raises(AuthoredToolError):
        _as_the_model("http://admin:hunter2@example.com/")


def test_a_control_character_cannot_split_the_request() -> None:
    with pytest.raises(AuthoredToolError):
        _as_the_model("http://example.com/\r\nX-Evil: 1")


# ---------------------------------------------------------------------------
# allowed — the house
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.5/bins?street={{ street }}",
        "http://10.0.0.4/api/documents/",
        "http://172.16.3.9:8080/x",
        "http://paperless.lan/api/documents/?query={{ query }}",
        "https://api.example.com/v1/forecast",
        # The host itself is a template: there is no host to judge yet, and
        # refusing this would ban a legitimate shape.
        "http://{{ host }}/x",
    ],
)
def test_the_lan_is_reachable_because_that_is_where_the_house_is(url: str) -> None:
    """RFC1918 is deliberately allowed, even for the model. See helpers/ssrf.py."""
    assert _as_the_model(url)["service"]["url"] == url


@pytest.mark.parametrize(
    "url",
    [
        # photon, the offline geocoder — the shipped example tool names it.
        "http://127.0.0.1:2322/api?q={{ query }}",
        "http://127.0.0.1:8888/search",  # SearXNG
        "http://localhost:8210/fetch",  # jarvis-browser
    ],
)
def test_the_console_may_still_name_this_box(url: str) -> None:
    """The operator's own loopback services are the ordinary case.

    This whole stack lives on one host. Refusing loopback outright would have
    banned the documented example (`config/examples/house/example.tool.yaml`,
    which points at photon on 127.0.0.1:2322) in order to stop the model, and a
    guard that breaks the shipped example is a guard that gets deleted.
    """
    assert _as_the_console(url)["service"]["url"] == url


def test_the_split_is_the_only_difference_between_the_two_callers() -> None:
    """One url, two verdicts, decided by who wrote it and nothing else."""
    url = "http://127.0.0.1:8080/api/services/lock/unlock"
    assert _as_the_console(url)
    with pytest.raises(AuthoredToolError):
        _as_the_model(url)


def test_a_stored_tool_still_loads_after_a_restart() -> None:
    """Re-validating on load must not delete what the console legitimately saved.

    `AuthoredToolStore.async_load` re-runs `validate` over everything on disk.
    If that path had picked up the model's stricter rule, every loopback tool
    the operator had ever created would vanish on the next restart — silently,
    because a failed entry is dropped with a log line.
    """
    stored = _manifest("http://127.0.0.1:2322/api?q={{ query }}")
    assert validate(dict(stored))["name"] == "some_tool"


def test_the_repos_own_worked_example_is_not_refused() -> None:
    """`config/examples/house/example.tool.yaml` must keep working.

    It names photon on `127.0.0.1:2322`, and that is the case that decided the
    shape of this whole guard. A `*.tool.yaml` is the OPERATOR's file — it is
    read by `build_yaml_tool`, never by the model's path — so loopback in it is
    a person naming a service on their own box.

    An example the validator rejects is worse than no example: a documented
    shape that cannot work is exactly the failure `create_tool` already had
    once, and this asserts the guard did not introduce a second one.
    """
    import yaml

    path = (
        Path(__file__).resolve().parents[1]
        / "config/examples/house/example.tool.yaml"
    )
    if not path.is_file():  # pragma: no cover - the example may be renamed
        pytest.skip(f"{path} is not there any more")

    checked = 0
    for spec in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if not isinstance(spec, dict) or "service" not in spec:
            continue
        url = str((spec.get("service") or {}).get("url") or "")
        if not url:
            continue
        # The operator's route, which is the one this file travels.
        assert validate(dict(spec))["service"]["url"] == url
        checked += 1

    assert checked, "the example carries no tool; this check found nothing"


# ---------------------------------------------------------------------------
# the mirror
# ---------------------------------------------------------------------------
def _desktop_source() -> str | None:
    path = (
        Path(__file__).resolve().parents[2]
        / "jarvis-desktop/jarvis_desktop/actions/ssrf.py"
    )
    return path.read_text(encoding="utf-8") if path.is_file() else None


def test_the_address_parsers_still_match_the_desktop_agents() -> None:
    """The fiddly half is a copy, so it must stay a copy.

    `parse_ipv4` exists to see the addresses a resolver sees and a reader does
    not — decimal, octal, hex, IPv4-in-IPv6. Three independent opinions about
    that would be three subtly different guards. The policy above them is
    allowed to differ (and does); this is about the reading.
    """
    source = _desktop_source()
    if source is None:  # pragma: no cover - desktop package not checked out
        pytest.skip("jarvis-desktop is not present")

    for name in ("_parse_ipv4_part", "parse_ipv4", "looks_like_ipv6", "_ipv4_compatible"):
        theirs = _function_body(source, name)
        ours = _function_body(
            Path(ssrf.__file__).read_text(encoding="utf-8"), name
        )
        assert theirs and ours, f"{name} is missing from one of the two modules"
        assert theirs == ours, (
            f"{name} has drifted between jarvis-core and jarvis-desktop. These "
            "read attacker-chosen addresses; they are a copy on purpose."
        )


def test_the_policies_differ_on_purpose_and_say_so() -> None:
    """If somebody makes core block RFC1918 too, they should have to mean it.

    This is the counterweight to the mirror test above: the parsers must not
    drift, and the *policy* must not be quietly conformed to the desktop's
    either. The house being reachable is a product decision, and one commit
    that "fixed the inconsistency" would take it away.
    """
    private = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    blocked = {str(net) for net in ssrf._BLOCKED_V4}
    assert not blocked & set(private), (
        "jarvis-core has started blocking the LAN. A tool pointing at a NAS or "
        "a bulb is the ordinary case here — see the module docstring before "
        "changing this."
    )
    assert "127.0.0.0/8" in blocked
    assert "169.254.0.0/16" in blocked

    source = _desktop_source()
    if source is None:  # pragma: no cover
        pytest.skip("jarvis-desktop is not present")
    assert '"192.168.0.0/16"' in source, (
        "the desktop agent has stopped blocking the LAN; it fetches public "
        "pages and should still block it"
    )


def _function_body(source: str, name: str) -> str | None:
    """The text of one top-level `def`, comments and blank lines removed."""
    match = re.search(
        rf"^def {re.escape(name)}\(.*?(?=^def |^class |\Z)", source, re.S | re.M
    )
    if not match:
        return None
    lines = []
    in_doc = False
    for line in match.group(0).splitlines():
        stripped = line.strip()
        # Docstrings are prose and are allowed to differ — each module explains
        # itself in its own terms. Only the code is a copy.
        if in_doc:
            if stripped.endswith('"""'):
                in_doc = False
            continue
        if stripped.startswith('"""'):
            if not (stripped.endswith('"""') and len(stripped) > 3):
                in_doc = True
            continue
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines)
