"""Where a model-authored URL is not allowed to point.

`create_tool` lets Jarvis write itself a new capability, and a capability is an
HTTP call. `authored_tools.validate` checked that the url started with `http://`
or `https://` and nothing else — so a tool the model wrote and a human waved
through could name:

    http://127.0.0.1:8080/api/...     jarvis-core's own API
    http://169.254.169.254/           cloud instance metadata, and its creds
    http://192.168.1.1/               the router's admin page
    http://2130706433/                the same loopback, written as an integer

A human approving a `create_tool` reads a manifest. They do not resolve its
host, and "does 2130706433 mean localhost" is not a question anybody should be
asked at an approval prompt. So the check belongs here, before the prompt.

## This is the literal half only

`check` inspects the URL **as written**. It cannot see where a *name* resolves
to, so `http://my-nas.local/` is allowed here and would need a resolve-and-check
at call time to be safe. That second layer is real work in the request path and
is not in this module; what is here is honest about it via
`Check.needs_dns_check`, which is True whenever the host is a name rather than a
literal. Nothing consumes that flag yet — the caller that will is the tool
executor.

## The LAN is allowed, and that is the whole point

This deliberately does **not** block RFC1918. `jarvis-desktop`'s guard does,
correctly, because its `http_request` exists to fetch a public page and a
desktop agent has no business reaching your router. jarvis-core is the opposite:
it is a house assistant, and a tool pointing at `http://192.168.1.5/bins` or a
NAS or a Paperless instance is the *normal* case — the repo's own worked
example, `config/examples/house/example.tool.yaml`, is exactly that. A guard
that banned it would be a guard nobody could ship with.

What is blocked is the set that has no legitimate use from a model-authored
tool: **loopback**, because jarvis-core's own API, the orchestrator and the
browser service all listen there behind their own credentials, and a tool
reaching them is the model reaching around its own gates; **link-local**,
because that is where cloud instance metadata and its credentials live; and the
unroutable oddities. Private LAN addresses are none of those.

### The limit this leaves, stated rather than implied

If jarvis-core is reachable on a LAN address rather than loopback, a tool may
name it. Closing that needs the deployment's own address, which this module is
not given; `allowed_hosts` is the seam where the inverse would go. And a *name*
is not resolved here at all — see above.

## Mirrors

The parsing — `parse_ipv4` and its `inet_aton` spellings, the IPv6 unwrapping —
is a deliberate copy of `jarvis-desktop/jarvis_desktop/actions/ssrf.py`, which
mirrors `android-app/.../automation/actions/SsrfGuard.kt`. That half is fiddly
and security-critical and a third independent opinion about how libc parses
`0177.0.0.1` would be worse than a copy that is checked. `tests/test_ssrf.py`
reads the desktop module's source and fails when the parsers disagree. The
*policy* above is this module's own, for the reason given.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlsplit

__all__ = [
    "Check",
    "check",
    "is_blocked_host_name",
    "is_blocked_ip",
    "is_ip_literal",
    "looks_like_ipv6",
    "parse_ipv4",
]


@dataclass(frozen=True)
class Check:
    allowed: bool
    reason: str | None = None
    scheme: str | None = None
    host: str | None = None
    #: True when `host` is a name, so a caller in the request path must resolve
    #: it and re-check every address before connecting.
    needs_dns_check: bool = False

    def __bool__(self) -> bool:
        return self.allowed


#: Names that never legitimately appear in a URL the model chose.
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

#: IPv4 ranges a model-authored tool must never contact.
#:
#: RFC1918 (10/8, 172.16/12, 192.168/16) is deliberately ABSENT — see the module
#: docstring. This is a house assistant; the LAN is where the house is.
_BLOCKED_V4 = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",  # "this network" — and 0.0.0.0 routes to loopback
        "127.0.0.0/8",  # loopback: jarvis-core's own API and its neighbours
        "169.254.0.0/16",  # link-local, incl. 169.254.169.254 metadata
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # TEST-NET-1
        "198.18.0.0/15",  # benchmarking
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, incl. 255.255.255.255
    )
)

#: IPv6 equivalents. `fc00::/7` (unique local) is the v6 RFC1918 and is absent
#: for the same reason — except that EC2's IMDS lives at `fd00:ec2::254`, which
#: is named explicitly so the metadata service stays blocked without taking the
#: whole ULA range with it.
_BLOCKED_V6 = tuple(
    ipaddress.ip_network(net)
    for net in (
        "::/128",  # unspecified
        "::1/128",  # loopback
        "fe80::/10",  # link-local
        "fd00:ec2::254/128",  # EC2 IMDS over v6
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
    """Dotted-quad plus the legacy spellings ``inet_aton`` honours.

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


def _ipv4_compatible(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    packed = addr.packed
    if packed[:12] == b"\x00" * 12 and packed[12:] != b"\x00\x00\x00\x00":
        return ipaddress.IPv4Address(packed[12:])
    return None


def is_blocked_ip(address: str) -> bool:
    """True when this address must never be contacted.

    An IPv6 string that will not parse fails closed (blocked); a string that is
    not an address at all is not this function's business and returns False.
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


def check(raw_url: object, allowed_hosts: Iterable[str] = ()) -> Check:
    """Literal inspection of a URL. See the module docstring for what it cannot see.

    ``allowed_hosts`` are exempt from the private-range block, for the case
    where the deployment genuinely means to reach something on its own LAN.
    Compared case-insensitively.
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
        return Check(
            False, f"only http and https are allowed (got {scheme or 'no scheme'})"
        )
    if parts.username or parts.password or "@" in parts.netloc.split("]")[-1]:
        return Check(False, "credentials in the url are not allowed", scheme=scheme)

    try:
        host = (parts.hostname or "").strip()
    except ValueError:
        return Check(False, "malformed host in url", scheme=scheme)
    if not host:
        return Check(False, "url has no host", scheme=scheme)

    exempt = {h.strip().lower().rstrip(".") for h in allowed_hosts if h}
    if host.lower().rstrip(".") in exempt:
        return Check(True, scheme=scheme, host=host)

    if is_blocked_host_name(host):
        return Check(
            False, f"{host} is this machine or a metadata service", scheme=scheme, host=host
        )

    if is_ip_literal(host):
        if is_blocked_ip(host):
            return Check(
                False,
                f"{host} is a loopback, private, link-local or metadata address",
                scheme=scheme,
                host=host,
            )
        return Check(True, scheme=scheme, host=host)

    # A name. Allowed here, and the caller in the request path is the one that
    # can resolve it — see the module docstring.
    return Check(True, scheme=scheme, host=host, needs_dns_check=True)
