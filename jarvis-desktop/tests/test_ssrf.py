"""The SSRF guard.

Two layers are tested separately, because they defend against different things:
the literal check catches a URL that *says* 127.0.0.1 in any of the spellings a
libc resolver accepts, and the DNS re-check catches a perfectly ordinary
hostname that *resolves* there. A guard with only the first is defeated by
registering a domain; a guard with only the second is defeated by typing the
address in octal.
"""

from __future__ import annotations

import pytest

from jarvis_desktop.actions import ssrf


# --- schemes and shapes -----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:11211/",
        "ftp://example.com/x",
        "javascript:alert(1)",
        "data:text/html,<script>",
        "ws://example.com/",
        "//example.com/x",
        "example.com/x",
        "",
        "   ",
    ],
)
def test_non_http_schemes_are_refused(url):
    assert not ssrf.check(url).allowed


def test_credentials_in_the_url_are_refused():
    assert not ssrf.check("http://user:pass@example.com/").allowed
    assert not ssrf.check("https://admin@example.com/").allowed


def test_control_characters_are_refused():
    assert not ssrf.check("http://exa\nmple.com/").allowed
    assert not ssrf.check("http://example.com/\r\nHost: evil").allowed


def test_non_string_input_is_refused():
    for raw in (None, 42, [], {}, b"http://example.com"):
        assert not ssrf.check(raw).allowed


# --- loopback and private literals, in every spelling -----------------------


@pytest.mark.parametrize(
    "host",
    [
        # plain
        "127.0.0.1",
        "127.1.2.3",
        "0.0.0.0",
        "10.0.0.5",
        "172.16.4.4",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # the cloud metadata address
        "100.64.0.1",  # CGNAT
        "198.18.0.1",  # benchmarking
        "224.0.0.1",  # multicast
        "255.255.255.255",
        # legacy inet_aton spellings a resolver still accepts
        "2130706433",  # 127.0.0.1 as a decimal
        "0177.0.0.1",  # octal
        "0x7f.0.0.1",  # hex
        "0x7f000001",
        "127.1",  # two-part form
        "127.0.1",  # three-part form
        "2852039166",  # 169.254.169.254
        # v6
        "[::1]",
        "[::]",
        "[fe80::1]",
        "[fc00::1]",
        "[fd00:ec2::254]",  # EC2 IMDS over IPv6
        "[ff02::1]",
        "[::ffff:127.0.0.1]",  # v4-mapped
        "[::ffff:169.254.169.254]",
        "[::127.0.0.1]",  # v4-compatible
        "[64:ff9b::7f00:1]",  # NAT64
    ],
)
def test_private_and_loopback_literals_are_blocked(host):
    result = ssrf.check(f"http://{host}/anything")
    assert not result.allowed, f"{host} was allowed"
    assert "blocked" in (result.reason or "")


@pytest.mark.parametrize(
    "host", ["localhost", "LOCALHOST", "foo.localhost", "localhost.", "ip6-localhost"]
)
def test_loopback_names_are_blocked(host):
    assert not ssrf.check(f"http://{host}/").allowed


@pytest.mark.parametrize(
    "host",
    ["metadata", "metadata.google.internal", "instance-data", "metadata.azure.com"],
)
def test_metadata_names_are_blocked(host):
    assert not ssrf.check(f"http://{host}/computeMetadata/v1/").allowed


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("2130706433", 0x7F000001),
        ("0177.0.0.1", 0x7F000001),
        ("0x7f.0.0.1", 0x7F000001),
        ("127.0.0.1", 0x7F000001),
        ("127.1", 0x7F000001),
        ("1.2.3.4", 0x01020304),
        ("example.com", None),
        ("1.2.3.4.5", None),
        ("999.1.1.1", None),
        ("", None),
    ],
)
def test_legacy_ipv4_spellings_parse_the_way_libc_does(host, expected):
    assert ssrf.parse_ipv4(host) == expected


def test_unparseable_ipv6_fails_closed():
    # Colons plus hex digits: it claims to be v6, will not parse, so it is blocked.
    assert ssrf.is_blocked_ip("1:2:3:4:5:6:7:8:9") is True
    assert ssrf.is_blocked_ip("::1::2") is True
    # And through the URL parser: a bracketed literal that will not parse is
    # refused, never handed to the resolver as if it were a hostname.
    for url in ("http://[::gg::1]/", "http://[::1::2]/", "http://[1:2:3:4:5:6:7:8:9]/"):
        assert not ssrf.check(url).allowed, url


# --- what is allowed --------------------------------------------------------


@pytest.mark.parametrize("host", ["1.1.1.1", "8.8.8.8", "93.184.216.34", "[2606:4700::1111]"])
def test_public_literals_are_allowed(host):
    result = ssrf.check(f"https://{host}/")
    assert result.allowed
    assert result.needs_dns_check is False


def test_a_hostname_is_allowed_but_flagged_for_a_dns_recheck():
    result = ssrf.check("https://example.com/page")
    assert result.allowed
    assert result.needs_dns_check is True
    assert result.host == "example.com"
    assert result.port == 443


def test_ports_are_parsed():
    assert ssrf.check("http://example.com:8080/").port == 8080
    assert ssrf.check("http://example.com/").port == 80
    assert ssrf.check("https://example.com/").port == 443


# --- the jarvis-core exemption ----------------------------------------------


def test_the_configured_server_is_exempt():
    """jarvis-core lives on the LAN; we already talk to it over an
    authenticated socket, so it stays reachable."""
    result = ssrf.check("http://192.168.1.50:8080/api/", allowed_hosts=["192.168.1.50"])
    assert result.allowed
    assert result.exempt is True
    assert result.needs_dns_check is False


def test_the_exemption_is_exact_not_a_suffix_match():
    """A near-miss host never inherits the jarvis-core exemption."""
    # Another private address is blocked outright.
    assert not ssrf.check("http://192.168.1.51/", allowed_hosts=["192.168.1.50"]).allowed
    # Strings that merely *contain* the exempt host are names, not the server:
    # not exempt, and still subject to the DNS re-check before anything opens a
    # socket to them.
    for url in ("http://192.168.1.500/", "http://evil.192.168.1.50/", "http://192.168.1.50.evil.com/"):
        check = ssrf.check(url, allowed_hosts=["192.168.1.50"])
        assert check.exempt is False, url
        assert check.needs_dns_check is True, url
        resolved = ssrf.resolve_and_check(
            url, allowed_hosts=["192.168.1.50"], resolver=lambda host: ["192.168.1.50"]
        )
        assert not resolved.allowed, url


def test_the_exemption_is_case_insensitive_and_ignores_a_trailing_dot():
    assert ssrf.check("http://Jarvis.LAN/", allowed_hosts=["jarvis.lan"]).exempt
    assert ssrf.check("http://jarvis.lan./", allowed_hosts=["jarvis.lan"]).exempt


# --- the DNS re-check -------------------------------------------------------


def test_a_name_that_resolves_to_loopback_is_refused():
    """The only defence against `evil.example.com A 127.0.0.1`."""
    result = ssrf.resolve_and_check(
        "https://evil.example.com/", resolver=lambda host: ["127.0.0.1"]
    )
    assert not result.allowed
    assert "resolves to" in (result.reason or "")


def test_a_name_with_one_bad_address_among_good_ones_is_refused():
    result = ssrf.resolve_and_check(
        "https://mixed.example.com/",
        resolver=lambda host: ["93.184.216.34", "169.254.169.254"],
    )
    assert not result.allowed


def test_a_name_that_resolves_publicly_is_allowed():
    result = ssrf.resolve_and_check(
        "https://example.com/", resolver=lambda host: ["93.184.216.34", "2606:2800:220::1"]
    )
    assert result.allowed
    assert result.needs_dns_check is False


def test_a_resolver_failure_is_a_refusal_not_an_allow():
    def boom(host):
        raise OSError("no such host")

    assert not ssrf.resolve_and_check("https://example.com/", resolver=boom).allowed
    assert not ssrf.resolve_and_check("https://example.com/", resolver=lambda h: []).allowed


def test_the_exempt_host_skips_dns_entirely():
    def boom(host):  # pragma: no cover - must never be called
        raise AssertionError("the exempt host should not be resolved")

    result = ssrf.resolve_and_check(
        "http://jarvis.lan:8080/", allowed_hosts=["jarvis.lan"], resolver=boom
    )
    assert result.allowed


def test_a_blocked_literal_never_reaches_the_resolver():
    def boom(host):  # pragma: no cover
        raise AssertionError("a blocked literal should not be resolved")

    assert not ssrf.resolve_and_check("http://127.0.0.1/", resolver=boom).allowed
