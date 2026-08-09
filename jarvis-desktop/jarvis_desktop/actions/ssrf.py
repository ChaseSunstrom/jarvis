"""SSRF guard for ``http_request``.

``http_request`` exists so Jarvis can fetch a public page or poke a public API.
It must not become a proxy into this machine's own loopback, the local network,
or a cloud metadata endpoint — because the thing choosing the URL is an LLM that
reads attacker-controlled text.

Two layers, both required:

1. :func:`check` — literal inspection of the URL. Blocks non-HTTP schemes,
   embedded credentials, and any host that is *written* as a loopback, private,
   link-local, CGNAT, multicast or metadata address, including the decimal
   (``http://2130706433``), octal (``0177.0.0.1``), hex (``0x7f.0.0.1``) and
   IPv4-mapped-IPv6 (``[::ffff:127.0.0.1]``) spellings that ``inet_aton`` still
   honours.
2. :func:`is_blocked_ip` — re-checked by the caller against every address the
   host actually resolves to, which is the only defence against a DNS name that
   points at 127.0.0.1.

The single exemption is the configured jarvis-core host: that is the server we
already talk to over an authenticated socket, so it stays reachable even though
it lives on the LAN.

Mirrors ``android-app/.../automation/actions/SsrfGuard.kt``.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence
from urllib.parse import urlsplit

__all__ = ["Check", "check", "is_blocked_ip", "parse_ipv4", "resolve_and_check"]


@dataclass(frozen=True)
class Check:
    allowed: bool
    reason: str | None = None
    scheme: str | None = None
    host: str | None = None
    port: int = -1
    #: True when the host matched the jarvis-core allowlist.
    exempt: bool = False
    #: True when ``host`` is a name, so the caller must resolve and re-check.
    needs_dns_check: bool = False

    def __bool__(self) -> bool:
        return self.allowed


#: Names that never legitimately appear in an LLM-chosen URL.
_METADATA_NAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
        "metadata.azure.com",
        "169.254.169.254.nip.io",
    }
)

#: IPv4 ranges that must never be contacted.
_BLOCKED_V4 = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",  # "this network"
        "10.0.0.0/8",  # RFC1918
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local, incl. 169.254.169.254 metadata
        "172.16.0.0/12",  # RFC1918
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # TEST-NET-1
        "192.168.0.0/16",  # RFC1918
        "198.18.0.0/15",  # benchmarking
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, incl. 255.255.255.255
    )
)

_BLOCKED_V6 = tuple(
    ipaddress.ip_network(net)
    for net in (
        "::/128",  # unspecified
        "::1/128",  # loopback
        "fe80::/10",  # link-local
        "fc00::/7",  # unique local (incl. fd00:ec2::254, EC2 IMDS over v6)
        "ff00::/8",  # multicast
        "64:ff9b::/96",  # NAT64
        "2001:db8::/32",  # documentation
    )
)


def is_blocked_host_name(host: str) -> bool:
    h = host.strip().rstrip(".").lower()
    if h == "localhost" or h.endswith(".localhost"):
        return True
    if h in ("ip6-localhost", "ip6-loopback"):
        return True
    return h in _METADATA_NAMES


def _parse_ipv4_part(part: str) -> int | None:
    """One segment of an ``inet_aton``-style address: decimal, octal or hex."""
    if not part or len(part) > 20:
        return None
    try:
        if part[:2].lower() == "0x":
            body = part[2:]
            if not body:
                return None
            value = int(body, 16)
        elif len(part) > 1 and part[0] == "0":
            value = int(part[1:], 8)
        else:
            if not part.isdigit():
                return None
            value = int(part, 10)
    except ValueError:
        return None
    return value if value >= 0 else None


def parse_ipv4(host: str) -> int | None:
    """Parse dotted-quad plus the legacy spellings ``inet_aton`` honours.

    Returns the address as a 32-bit int, or None when it is not IPv4 at all.
    ``2130706433``, ``0177.0.0.1`` and ``0x7f.0.0.1`` all come back as
    ``127.0.0.1`` — libc-backed resolvers accept them, so the guard must too.
    """
    h = host.strip().rstrip(".")
    if not h:
        return None
    parts = h.split(".")
    if len(parts) > 4:
        return None
    values: list[int] = []
    for part in parts:
        value = _parse_ipv4_part(part)
        if value is None:
            return None
        values.append(value)
    n = len(values)
    if n == 1:
        return values[0] if values[0] <= 0xFFFFFFFF else None
    if n == 2:
        if values[0] <= 0xFF and values[1] <= 0xFFFFFF:
            return (values[0] << 24) | values[1]
        return None
    if n == 3:
        if values[0] <= 0xFF and values[1] <= 0xFF and values[2] <= 0xFFFF:
            return (values[0] << 24) | (values[1] << 16) | values[2]
        return None
    if all(v <= 0xFF for v in values):
        return (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | values[3]
    return None


def looks_like_ipv6(host: str) -> bool:
    h = host.strip().strip("[]")
    if ":" not in h:
        return False
    body = h.split("%", 1)[0]
    return bool(body) and all(
        ch == ":" or ch == "." or ch.isdigit() or ch.lower() in "abcdef" for ch in body
    )


def is_ip_literal(host: str) -> bool:
    """True when the string is written as an IPv4 or IPv6 literal, any spelling."""
    return parse_ipv4(host) is not None or looks_like_ipv6(host)


def is_blocked_ip(address: str) -> bool:
    """True when this address must never be contacted.

    Call it on the literal host AND on every address the host resolves to. An
    IPv6 string that will not parse fails closed (blocked); a string that is not
    an address at all is not this function's business and returns False.
    """
    v4 = parse_ipv4(address)
    if v4 is not None:
        addr4 = ipaddress.IPv4Address(v4)
        return any(addr4 in net for net in _BLOCKED_V4)

    if looks_like_ipv6(address):
        text = address.strip().strip("[]").split("%", 1)[0]
        try:
            addr6 = ipaddress.IPv6Address(text)
        except ValueError:
            return True  # unparseable v6 => fail closed
        if any(addr6 in net for net in _BLOCKED_V6):
            return True
        # IPv4-mapped (::ffff:a.b.c.d) and IPv4-compatible (::a.b.c.d) carry a
        # v4 address inside a v6 wrapper; unwrap and re-check.
        mapped = addr6.ipv4_mapped or _ipv4_compatible(addr6)
        if mapped is not None and any(mapped in net for net in _BLOCKED_V4):
            return True
        return False

    return False


def _ipv4_compatible(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    packed = addr.packed
    if packed[:12] == b"\x00" * 12 and packed[12:] != b"\x00\x00\x00\x00":
        return ipaddress.IPv4Address(packed[12:])
    return None


def check(raw_url: object, allowed_hosts: Iterable[str] = ()) -> Check:
    """Literal inspection of a URL.

    ``allowed_hosts`` are exempt from the private-range block — normally just
    the configured jarvis-core host. Compared case-insensitively.
    """
    if not isinstance(raw_url, str):
        return Check(False, "url is required")
    text = raw_url.strip()
    if not text:
        return Check(False, "url is required")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return Check(False, "url contains control characters")

    try:
        parts = urlsplit(text)
    except ValueError:
        return Check(False, "malformed url")

    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return Check(False, f"only http and https are allowed (got {scheme or 'no scheme'})")
    if parts.username or parts.password or "@" in (parts.netloc.split("]")[-1]):
        return Check(False, "credentials in the url are not allowed")

    try:
        raw_host = parts.hostname
    except ValueError:
        return Check(False, "malformed url")
    if not raw_host:
        return Check(False, "url has no host")
    host = raw_host.strip().strip("[]").rstrip(".").lower()
    if not host:
        return Check(False, "url has no host")

    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        return Check(False, "malformed port")

    exempt_set = {
        h.strip().rstrip(".").lower() for h in allowed_hosts if h and h.strip()
    }
    if host in exempt_set:
        return Check(True, None, scheme, host, port, exempt=True, needs_dns_check=False)

    if is_blocked_host_name(host):
        return Check(False, f"host {host} is blocked (loopback/metadata name)", scheme, host, port)

    # A host with a colon in it is trying to be an IPv6 literal. If it will not
    # parse as one, refuse it here rather than handing it to the resolver: a
    # string the guard cannot read is a string the guard cannot vouch for.
    if ":" in host and not looks_like_ipv6(host):
        return Check(False, f"host {host} is not a valid IPv6 literal", scheme, host, port)

    if is_ip_literal(host):
        if is_blocked_ip(host):
            return Check(
                False,
                f"address {host} is blocked (private/loopback/link-local/metadata)",
                scheme,
                host,
                port,
            )
        return Check(True, None, scheme, host, port, exempt=False, needs_dns_check=False)

    # A name we do not recognise: allowed only after the caller resolves it and
    # runs every resulting address through is_blocked_ip.
    return Check(True, None, scheme, host, port, exempt=False, needs_dns_check=True)


Resolver = Callable[[str], Sequence[str]]


def system_resolver(host: str) -> list[str]:
    """Every address ``host`` resolves to, v4 and v6."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def resolve_and_check(
    raw_url: object,
    allowed_hosts: Iterable[str] = (),
    resolver: Resolver = system_resolver,
) -> Check:
    """:func:`check` plus the DNS re-check, in one call.

    The resolver is injected so tests can point a name at 127.0.0.1 without
    touching the network.
    """
    result = check(raw_url, allowed_hosts)
    if not result.allowed or not result.needs_dns_check or result.host is None:
        return result
    try:
        addresses = list(resolver(result.host))
    except Exception as exc:  # noqa: BLE001 - any resolver failure is a refusal
        return Check(False, f"could not resolve {result.host}: {exc}", result.scheme, result.host, result.port)
    if not addresses:
        return Check(False, f"could not resolve {result.host}", result.scheme, result.host, result.port)
    for address in addresses:
        if is_blocked_ip(str(address)):
            return Check(
                False,
                f"blocked: {result.host} resolves to {address}, "
                "which is a private/loopback/metadata address",
                result.scheme,
                result.host,
                result.port,
            )
    return Check(True, None, result.scheme, result.host, result.port, needs_dns_check=False)
