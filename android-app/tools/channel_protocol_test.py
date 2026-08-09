#!/usr/bin/env python3
"""Executable spec for the Android command channel's safety logic.

Three rules decide whether the phone talks to a server at all, what tier a
command really has, and how fast commands may arrive. They live in Kotlin —

    app/src/main/kotlin/ai/jarvis/app/channel/LanHost.kt     (a)
    app/src/main/kotlin/ai/jarvis/app/channel/TierGuard.kt   (b)
    app/src/main/kotlin/ai/jarvis/app/channel/TokenBucket.kt (c)

— which this container has no Android SDK to compile, let alone run. So each
rule is written down twice: once in Kotlin and once here, where it executes.

  (a) isLanHost / checkUrl — plain HTTP is permitted to private space and
      nowhere else. Getting this wrong sends a bearer token across the internet
      in the clear.
  (b) the tier-raise-only rule — max(local, incoming). The server may make an
      action MORE dangerous and can never make one less dangerous.
  (c) the inbound token bucket — a prompt-injected server cannot flood the phone
      with consent prompts until the user taps one out of fatigue.

Each section re-implements the rule from the Kotlin and checks it against a
hand-written table of cases. The table is the point: an algorithm copied twice
can be wrong twice, but it cannot disagree with cases somebody wrote out by
hand. A final section greps the Kotlin to catch one copy being edited without
the other.

Run:      python3 android-app/tools/channel_protocol_test.py
Or:       python3 -m pytest android-app/tools/channel_protocol_test.py -q
"""

from __future__ import annotations

import math
import re
import sys
from itertools import product
from pathlib import Path
from urllib.parse import urlsplit

KOTLIN_DIR = Path(__file__).resolve().parent.parent / "app/src/main/kotlin/ai/jarvis/app/channel"
KOTLIN_LANHOST = KOTLIN_DIR / "LanHost.kt"
KOTLIN_TIERGUARD = KOTLIN_DIR / "TierGuard.kt"
KOTLIN_BUCKET = KOTLIN_DIR / "TokenBucket.kt"
KOTLIN_CHANNEL = KOTLIN_DIR / "JarvisChannel.kt"
KOTLIN_CONFIG = KOTLIN_DIR / "ChannelConfig.kt"


# ===========================================================================
# (a) isLanHost — mirrored from LanHost.kt
# ===========================================================================

LAN_CLASSES = {
    "LOOPBACK",
    "PRIVATE_V4",
    "LINK_LOCAL_V4",
    "CGNAT_V4",
    "UNIQUE_LOCAL_V6",
    "LINK_LOCAL_V6",
    "PRIVATE_NAME",
}
NON_LAN_CLASSES = {"PUBLIC", "INVALID"}

PRIVATE_SUFFIXES = (
    ".local", ".lan", ".home", ".home.arpa", ".internal", ".intranet",
    ".localdomain", ".private",
)


def normalize(host: str | None) -> str | None:
    if host is None:
        return None
    h = host.strip().lower()
    if not h:
        return None
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    h = h.split("%", 1)[0]
    h = h.rstrip(".")
    if not h:
        return None
    if any(c.isspace() for c in h) or "/" in h or "@" in h:
        return None
    return h


def classify_ipv4(h: str) -> str | None:
    """None when `h` is not an IPv4 literal attempt at all."""
    parts = h.split(".")
    if len(parts) != 4:
        return None
    if not all(p and p.isdigit() and p.isascii() for p in parts):
        return None

    octets = []
    for p in parts:
        if len(p) > 3:
            return "INVALID"
        if len(p) > 1 and p[0] == "0":  # octal-looking: 0177.0.0.1 is 127.0.0.1
            return "INVALID"
        v = int(p)
        if v > 255:
            return "INVALID"
        octets.append(v)

    a, b = octets[0], octets[1]
    if a == 0:
        return "INVALID"
    if a == 127:
        return "LOOPBACK"
    if a == 10:
        return "PRIVATE_V4"
    if a == 172 and 16 <= b <= 31:
        return "PRIVATE_V4"
    if a == 192 and b == 168:
        return "PRIVATE_V4"
    if a == 169 and b == 254:
        return "LINK_LOCAL_V4"
    if a == 100 and 64 <= b <= 127:
        return "CGNAT_V4"
    return "PUBLIC"


def is_v4_embedding_prefix(prefix: str) -> bool:
    """True only for `::` and `::ffff:` (compressed or long-hand).

    Those are the two prefixes whose trailing dotted quad really is an IPv4
    address. Any other prefix means an ordinary IPv6 address that merely happens
    to be written with a dotted tail, and reading the tail there is a cleartext
    bypass — `2001:4860:4860::10.0.0.1` is globally routable.
    """
    if not prefix.endswith(":"):
        return False
    compressed = prefix.startswith("::")
    groups = [g for g in prefix.split(":") if g]
    if compressed and len(groups) > 1:
        return False
    if not compressed and len(groups) != 6:
        return False
    for i, group in enumerate(groups):
        if len(group) > 4:
            return False
        try:
            value = int(group, 16)
        except ValueError:
            return False
        if value == 0:
            continue
        if value != 0xFFFF or i != len(groups) - 1:
            return False
    return True


def classify_ipv6(h: str) -> str | None:
    if ":" not in h:
        return None

    bare = h.replace(":", "")
    if not bare:
        return "INVALID"          # "::" — the unspecified address
    if all(c == "0" for c in bare):
        return "INVALID"
    # The endsWith guard matters: without it "1::" (= 0001::) reduces to "1".
    if h.endswith(":1") and bare.lstrip("0") == "1":
        return "LOOPBACK"

    tail = h.rsplit(":", 1)[-1]
    if "." in tail:                # ::ffff:192.168.1.10
        prefix = h[: len(h) - len(tail)]
        if is_v4_embedding_prefix(prefix):
            return classify_ipv4(tail) or "INVALID"
        # else fall through and classify it as the IPv6 address it actually is

    first = h.split(":", 1)[0]
    if not first:                  # starts with "::"
        return "PUBLIC"
    if len(first) > 4:
        return "INVALID"
    try:
        value = int(first, 16)
    except ValueError:
        return "INVALID"
    if 0xFC00 <= value <= 0xFDFF:
        return "UNIQUE_LOCAL_V6"   # fc00::/7
    if 0xFE80 <= value <= 0xFEBF:
        return "LINK_LOCAL_V6"     # fe80::/10
    return "PUBLIC"


def classify(host: str | None) -> str:
    h = normalize(host)
    if h is None:
        return "INVALID"
    if h == "localhost":
        return "LOOPBACK"

    v6 = classify_ipv6(h)
    if v6 is not None:
        return v6
    v4 = classify_ipv4(h)
    if v4 is not None:
        return v4

    if any(h.endswith(s) for s in PRIVATE_SUFFIXES):
        return "PRIVATE_NAME"
    if "." not in h:
        return "PRIVATE_NAME"
    return "PUBLIC"


def is_lan_host(host: str | None) -> bool:
    return classify(host) in LAN_CLASSES


def check_url(url: str | None, acknowledged: frozenset[str] = frozenset()) -> bool:
    """True when the channel is allowed to dial this URL."""
    if not url or not url.strip():
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    scheme = (parts.scheme or "").lower()
    if not scheme:
        return False
    try:
        host = normalize(parts.hostname)
    except ValueError:
        return False
    if host is None:
        return False
    if scheme in ("https", "wss"):
        return True
    if scheme not in ("http", "ws"):
        return False
    if is_lan_host(host):
        return True
    return any(normalize(a) == host for a in acknowledged)


# --- the table -------------------------------------------------------------

HOST_TABLE = [
    # loopback
    ("localhost", "LOOPBACK"),
    ("127.0.0.1", "LOOPBACK"),
    ("127.255.255.254", "LOOPBACK"),
    ("::1", "LOOPBACK"),
    ("[::1]", "LOOPBACK"),
    ("0:0:0:0:0:0:0:1", "LOOPBACK"),
    # RFC1918
    ("10.0.0.1", "PRIVATE_V4"),
    ("10.255.255.255", "PRIVATE_V4"),
    ("192.168.2.10", "PRIVATE_V4"),
    ("172.16.0.1", "PRIVATE_V4"),
    ("172.31.255.255", "PRIVATE_V4"),
    # …and the addresses just outside 172.16/12, which is the classic off-by-one
    ("172.15.0.1", "PUBLIC"),
    ("172.32.0.1", "PUBLIC"),
    ("192.169.0.1", "PUBLIC"),
    ("11.0.0.1", "PUBLIC"),
    # link-local and CGNAT (WireGuard/Tailscale hand out 100.64/10)
    ("169.254.1.1", "LINK_LOCAL_V4"),
    ("169.253.1.1", "PUBLIC"),
    ("100.64.0.1", "CGNAT_V4"),
    ("100.127.255.255", "CGNAT_V4"),
    ("100.63.255.255", "PUBLIC"),
    ("100.128.0.1", "PUBLIC"),
    # IPv6
    ("fd00::1", "UNIQUE_LOCAL_V6"),
    ("fc00::1", "UNIQUE_LOCAL_V6"),
    ("fdff:ffff::1", "UNIQUE_LOCAL_V6"),
    ("fe80::1", "LINK_LOCAL_V6"),
    ("febf::1", "LINK_LOCAL_V6"),
    ("fec0::1", "PUBLIC"),          # site-local, deprecated and NOT private space
    ("2001:db8::1", "PUBLIC"),
    ("[2001:db8::1]", "PUBLIC"),
    ("fe80::1%wlan0", "LINK_LOCAL_V6"),
    ("::ffff:192.168.1.10", "PRIVATE_V4"),
    ("::ffff:8.8.8.8", "PUBLIC"),
    ("0:0:0:0:0:ffff:192.168.1.10", "PRIVATE_V4"),   # long-hand v4-mapped
    ("::192.168.1.10", "PRIVATE_V4"),                # deprecated v4-compatible
    # …and the regression: a dotted tail on a NON-v4-mapped prefix is just the
    # low 32 bits of an ordinary IPv6 address. Reading it as IPv4 called a
    # globally routable host "RFC1918" and permitted cleartext to it.
    ("2001:db8::192.168.1.1", "PUBLIC"),
    ("2001:4860:4860::10.0.0.1", "PUBLIC"),
    ("64:ff9b::192.168.1.1", "PUBLIC"),              # NAT64 well-known prefix
    ("fec0::10.0.0.1", "PUBLIC"),                    # site-local is not private
    ("::abcd:10.0.0.1", "PUBLIC"),                   # ::ffff: is the only marker
    ("fd00::192.168.1.1", "UNIQUE_LOCAL_V6"),        # a ULA really is private
    ("1::", "PUBLIC"),              # NOT loopback, despite reducing to "1"
    ("::", "INVALID"),
    # names
    ("jarvis.local", "PRIVATE_NAME"),
    ("JARVIS.LOCAL", "PRIVATE_NAME"),
    ("jarvis.lan", "PRIVATE_NAME"),
    ("nas.home.arpa", "PRIVATE_NAME"),
    ("box.internal", "PRIVATE_NAME"),
    ("jarvis", "PRIVATE_NAME"),     # single label: only a local resolver answers
    ("jarvis.lan.", "PRIVATE_NAME"),
    ("example.com", "PUBLIC"),
    ("evil.example.com", "PUBLIC"),
    ("notlocal.com", "PUBLIC"),
    # the interesting refusals
    ("0177.0.0.1", "INVALID"),      # octal for 127.0.0.1 to inet_aton
    ("010.0.0.1", "INVALID"),
    ("192.168.1.256", "INVALID"),
    ("1.2.3.4.5", "PUBLIC"),        # five labels: a NAME, and a public one
    ("0.0.0.0", "INVALID"),
    ("", "INVALID"),
    (None, "INVALID"),
    ("192.168.1.1 ", "PRIVATE_V4"),  # whitespace is trimmed
    ("host name", "INVALID"),
    ("user@evil.com", "INVALID"),
    ("evil.com/192.168.1.1", "INVALID"),
]

URL_TABLE = [
    # (url, allowed)
    ("http://192.168.2.10:8123", True),
    ("ws://192.168.2.10:8123/api/websocket", True),
    ("http://jarvis.local:8123", True),
    ("http://10.8.0.1:8123", True),
    ("http://100.64.3.2:8123", True),
    ("http://[fd00::1]:8123", True),
    ("https://jarvis.example.com", True),
    ("wss://jarvis.example.com/api/websocket", True),
    ("https://8.8.8.8", True),
    # the whole point of the rule
    ("http://jarvis.example.com", False),
    ("ws://jarvis.example.com/api/websocket", False),
    ("http://8.8.8.8", False),
    ("http://0177.0.0.1:8123", False),
    ("http://192.168.1.256:8123", False),
    # A public IPv6 address wearing an RFC1918 tail.
    ("http://[2001:4860:4860::10.0.0.1]:8123", False),
    ("ws://[2001:db8::192.168.1.1]:8123/api/websocket", False),
    ("https://[2001:4860:4860::10.0.0.1]:8123", True),   # TLS is fine anywhere
    # not a transport we speak
    ("file:///etc/passwd", False),
    ("ftp://192.168.2.10", False),
    ("javascript:alert(1)", False),
    ("", False),
    (None, False),
]


def test_host_classification_table():
    for host, expected in HOST_TABLE:
        got = classify(host)
        assert got == expected, f"classify({host!r}) = {got}, expected {expected}"


def test_lan_flag_follows_classification():
    for host, expected in HOST_TABLE:
        assert is_lan_host(host) == (expected in LAN_CLASSES), host
    assert LAN_CLASSES & NON_LAN_CLASSES == set()


def test_url_policy_table():
    for url, expected in URL_TABLE:
        got = check_url(url)
        assert got == expected, f"check_url({url!r}) = {got}, expected {expected}"


def test_cleartext_to_a_public_host_needs_an_explicit_acknowledgement():
    url = "http://jarvis.example.com:8123"
    assert not check_url(url)
    assert check_url(url, frozenset({"jarvis.example.com"}))
    # …and only for the host that was acknowledged.
    assert not check_url(url, frozenset({"other.example.com"}))
    # An acknowledgement never widens TLS-only rules to a different scheme.
    assert not check_url("ftp://jarvis.example.com", frozenset({"jarvis.example.com"}))


def test_a_dotted_tail_only_means_ipv4_for_the_v4_mapped_prefixes():
    """Regression: `2001:4860:4860::10.0.0.1` is public, not RFC1918.

    classifyIpv6 used to hand any address with a dotted quad straight to the
    IPv4 classifier. That reads the low 32 bits of a perfectly ordinary global
    IPv6 address as if they were the whole address, so a public host classified
    as LAN and `ws://` to it — bearer token and all — was permitted.
    """
    assert is_v4_embedding_prefix("::")
    assert is_v4_embedding_prefix("::ffff:")
    assert is_v4_embedding_prefix("0:0:0:0:0:ffff:")
    assert is_v4_embedding_prefix("0:0:0:0:0:0:")
    for prefix in ["2001:db8::", "64:ff9b::", "fd00::", "::abcd:", "ffff::", "0:0:ffff:", "x"]:
        assert not is_v4_embedding_prefix(prefix), prefix

    for host in ["2001:db8::192.168.1.1", "2001:4860:4860::10.0.0.1",
                 "64:ff9b::10.0.0.1", "fec0::192.168.1.1", "::abcd:10.0.0.1"]:
        assert not is_lan_host(host), host
        assert not check_url(f"http://[{host}]:8123"), host
        assert check_url(f"https://[{host}]:8123"), host

    # The genuinely v4-mapped forms still work, and a ULA is still a ULA.
    for host in ["::ffff:192.168.1.10", "0:0:0:0:0:ffff:192.168.1.10", "::192.168.1.10"]:
        assert is_lan_host(host), host
    assert is_lan_host("fd00::192.168.1.1")
    assert not is_lan_host("::ffff:8.8.8.8")


def test_tls_is_allowed_everywhere_and_cleartext_never_leaves_private_space():
    for host, cls in HOST_TABLE:
        if host is None or classify(host) == "INVALID":
            continue
        literal = f"[{host}]" if ":" in str(host) and not str(host).startswith("[") else host
        assert check_url(f"https://{literal}"), host
        assert check_url(f"http://{literal}") == (cls in LAN_CLASSES), host


# ===========================================================================
# (b) the tier-raise-only rule — mirrored from TierGuard.kt
# ===========================================================================

TIERS = ["AUTO", "NOTIFY", "CONFIRM"]  # order IS severity
WIRE = {1: "AUTO", 2: "NOTIFY", 3: "CONFIRM"}


def tier_parse(value) -> str | None:
    """Anything unrecognised is None = 'the server expressed no opinion'."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return WIRE.get(value)
    if isinstance(value, float):
        return WIRE.get(int(value)) if float(value).is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return WIRE.get(int(text))
        return {
            "AUTO": "AUTO", "TIER1": "AUTO",
            "NOTIFY": "NOTIFY", "TIER2": "NOTIFY",
            "CONFIRM": "CONFIRM", "TIER3": "CONFIRM",
        }.get(text.upper())
    return None


def tier_max(a: str, b: str) -> str:
    return a if TIERS.index(a) >= TIERS.index(b) else b


def tier_effective(local: str, incoming: str | None) -> str:
    return tier_max(local, incoming or "AUTO")


def tier_for_action(local_table: dict[str, str], action_id: str, incoming: str | None) -> str:
    # An action this device never advertised is treated as the most dangerous
    # tier there is, not as "unknown" and not as whatever the server claims.
    local = local_table.get(action_id, "CONFIRM")
    return tier_effective(local, incoming)


MANIFEST = {
    "get_battery": "AUTO",
    "media_pause": "AUTO",
    "set_alarm": "NOTIFY",
    "clipboard_write": "NOTIFY",
    "sms_send": "CONFIRM",
    "ui_click": "CONFIRM",
}


def test_incoming_tier_can_only_raise():
    for local, incoming in product(TIERS, [None] + TIERS):
        got = tier_effective(local, incoming)
        assert TIERS.index(got) >= TIERS.index(local), (local, incoming, got)
        assert got == tier_max(local, incoming or "AUTO")


def test_a_hostile_tier_field_cannot_downgrade_a_dangerous_action():
    """The headline case: the server insists an SMS is a Tier 1 read."""
    for claim in [1, 2, "1", "AUTO", "tier1", None, 0, -1, 99, 3.7, "", "yes", True, [1], {"tier": 1}]:
        parsed = tier_parse(claim)
        assert tier_for_action(MANIFEST, "sms_send", parsed) == "CONFIRM", claim
        assert tier_for_action(MANIFEST, "ui_click", parsed) == "CONFIRM", claim
    # A Tier-2 action cannot be talked down to Tier 1 either.
    for claim in [1, "AUTO", None, "garbage"]:
        assert tier_for_action(MANIFEST, "set_alarm", tier_parse(claim)) == "NOTIFY", claim


def test_the_server_may_raise():
    assert tier_for_action(MANIFEST, "get_battery", tier_parse(2)) == "NOTIFY"
    assert tier_for_action(MANIFEST, "get_battery", tier_parse(3)) == "CONFIRM"
    assert tier_for_action(MANIFEST, "set_alarm", tier_parse(3)) == "CONFIRM"


def test_an_unknown_action_is_tier_three():
    for claim in [None, 1, 2, 3, "AUTO", "garbage"]:
        assert tier_for_action(MANIFEST, "definitely_not_an_action", tier_parse(claim)) == "CONFIRM"
    assert tier_for_action({}, "get_battery", tier_parse(1)) == "CONFIRM"


def test_garbage_tier_values_parse_to_no_opinion():
    for claim in [0, 4, 99, -1, "", "  ", "three", "TIER4", None, 1.5, True, False, [], {}]:
        assert tier_parse(claim) is None, claim
    for wire, name in WIRE.items():
        assert tier_parse(wire) == name
        assert tier_parse(str(wire)) == name
        assert tier_parse(name) == name
        assert tier_parse(name.lower()) == name


def test_exhaustive_no_combination_ever_lowers():
    """Every action, every claim, every time: never below the local tier."""
    claims = [None, 0, 1, 2, 3, 4, "1", "AUTO", "CONFIRM", "nonsense", -1, 3.0]
    checked = 0
    for action, local in MANIFEST.items():
        for claim in claims:
            got = tier_for_action(MANIFEST, action, tier_parse(claim))
            assert TIERS.index(got) >= TIERS.index(local), (action, claim, got)
            checked += 1
    assert checked == len(MANIFEST) * len(claims)


# ===========================================================================
# (c) the token bucket — mirrored from TokenBucket.kt
# ===========================================================================


class TokenBucket:
    def __init__(self, capacity: float, refill_per_second: float,
                 start_ms: int = 0, initial_tokens: float | None = None):
        assert capacity > 0 and refill_per_second > 0
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self.tokens = self.capacity if initial_tokens is None else min(max(initial_tokens, 0.0), capacity)
        self.last_ms = start_ms

    def _refill(self, now_ms: int) -> None:
        elapsed = now_ms - self.last_ms
        self.last_ms = now_ms
        if elapsed <= 0:
            return
        self.tokens = min(self.capacity, self.tokens + elapsed / 1000.0 * self.refill_per_second)

    def peek(self, now_ms: int) -> float:
        self._refill(now_ms)
        return self.tokens

    def try_acquire(self, now_ms: int, cost: float = 1.0) -> bool:
        self._refill(now_ms)
        if self.tokens < cost:
            return False
        self.tokens -= cost
        return True

    def wait_ms(self, now_ms: int, cost: float = 1.0) -> int:
        self._refill(now_ms)
        if self.tokens >= cost:
            return 0
        return math.ceil((cost - self.tokens) / self.refill_per_second * 1000.0)

    def reset(self, now_ms: int) -> None:
        self.tokens = self.capacity
        self.last_ms = now_ms


DEFAULT_CAPACITY = 10.0
DEFAULT_REFILL = 1.0


def test_burst_then_refuse():
    b = TokenBucket(DEFAULT_CAPACITY, DEFAULT_REFILL, start_ms=0)
    for i in range(10):
        assert b.try_acquire(0), f"command {i} of the burst should pass"
    assert not b.try_acquire(0), "the 11th back-to-back command must be refused"
    assert not b.try_acquire(999), "still refused 1 ms before the first refill"
    assert b.try_acquire(1000), "one token a second later"
    assert not b.try_acquire(1000)


def test_a_flood_settles_to_the_sustained_rate():
    """1000 commands in one millisecond gets 10 through, not 1000."""
    b = TokenBucket(DEFAULT_CAPACITY, DEFAULT_REFILL, start_ms=0)
    allowed = sum(1 for _ in range(1000) if b.try_acquire(0))
    assert allowed == 10, allowed
    # Over the following minute, exactly 60 more.
    allowed = 0
    for second in range(1, 61):
        for _ in range(50):
            if b.try_acquire(second * 1000):
                allowed += 1
    assert allowed == 60, allowed


def test_refill_is_capped_at_capacity():
    b = TokenBucket(DEFAULT_CAPACITY, DEFAULT_REFILL, start_ms=0)
    assert b.try_acquire(0)
    assert b.peek(10_000_000) == DEFAULT_CAPACITY, "an idle hour does not bank an hour of tokens"


def test_a_clock_that_goes_backwards_grants_nothing():
    """SystemClock.elapsedRealtime does not go backwards; a test double might."""
    b = TokenBucket(DEFAULT_CAPACITY, DEFAULT_REFILL, start_ms=100_000)
    for _ in range(10):
        assert b.try_acquire(100_000)
    assert not b.try_acquire(100_000)
    assert not b.try_acquire(0), "rewinding the clock must not hand out a refill"
    assert not b.try_acquire(50), "…and the bucket must not have banked a huge delta either"
    assert b.try_acquire(1050), "…but it still refills normally from where it re-anchored"


def test_wait_ms_tells_the_server_when_to_come_back():
    b = TokenBucket(DEFAULT_CAPACITY, DEFAULT_REFILL, start_ms=0)
    for _ in range(10):
        b.try_acquire(0)
    assert b.wait_ms(0) == 1000
    assert b.wait_ms(500) == 500
    assert b.wait_ms(1000) == 0

    # A fresh bucket, because wait_ms() refills as a side effect and this case
    # is about the cost, not about rewinding the clock.
    drained = TokenBucket(DEFAULT_CAPACITY, DEFAULT_REFILL, start_ms=0)
    for _ in range(10):
        drained.try_acquire(0)
    assert drained.wait_ms(0, cost=3.0) == 3000


def test_partial_refill_is_fractional_not_rounded_down():
    b = TokenBucket(4.0, 2.0, start_ms=0)
    for _ in range(4):
        assert b.try_acquire(0)
    assert not b.try_acquire(0)
    assert not b.try_acquire(499), "0.998 tokens is not a token"
    assert b.try_acquire(500), "0.5 s at 2/s is exactly one token"


def test_reset_refills_but_only_on_a_fresh_socket():
    b = TokenBucket(DEFAULT_CAPACITY, DEFAULT_REFILL, start_ms=0)
    for _ in range(10):
        b.try_acquire(0)
    assert not b.try_acquire(0)
    b.reset(0)
    assert b.try_acquire(0), "a new connection starts with a full burst allowance"


# ===========================================================================
# (d) the handshake — mirrored from JarvisChannel.onText/onResult/onDeviceCommand
# ===========================================================================
#
# Commands are accepted on exactly one condition: the socket authenticated AND
# the registration WE sent came back acknowledged. The interesting failure is
# what counts as "came back acknowledged" — see the bare-result case below.


class Handshake:
    """The bits of JarvisChannel's session state that gate `device_command`."""

    NO_ID = -1

    def __init__(self):
        self.authed = False
        self.registered = False
        self.register_id = self.NO_ID     # Session.registerId, unset
        self.next_request_id = 1
        self.sent = []
        self.executed = []

    # --- outbound ---
    def _send_register(self):
        # Registration is only ever sent on an authenticated socket, so the id
        # it allocates cannot be matched by a result that arrived first.
        if not self.authed:
            return
        self.register_id = self.next_request_id
        self.next_request_id += 1
        self.sent.append(("register", self.register_id))

    # --- inbound ---
    def on_frame(self, frame: dict):
        kind = frame.get("type")
        if kind == "auth_required":
            self.sent.append(("auth", None))
        elif kind == "auth_ok":
            self.authed = True
            self._send_register()
        elif kind == "auth_invalid":
            self.authed = False
            self.registered = False
        elif kind == "result":
            self._on_result(frame)
        elif kind == "device_command":
            self._on_device_command(frame)

    def _on_result(self, frame: dict):
        # An absent / null / non-integer id is NOT a reply to anything we sent.
        raw = frame.get("id", None)
        rid = raw if isinstance(raw, int) and not isinstance(raw, bool) else self.NO_ID
        if rid < 0:
            return
        if not self.authed or self.register_id < 0 or rid != self.register_id:
            return
        if frame.get("success") is True:
            self.registered = True

    def _on_device_command(self, frame: dict):
        if not self.authed or not self.registered:
            return
        self.executed.append(frame.get("command_id"))


def test_the_happy_path_reaches_ready():
    h = Handshake()
    h.on_frame({"type": "auth_required"})
    assert h.sent == [("auth", None)]
    h.on_frame({"type": "auth_ok"})
    assert h.sent[-1] == ("register", 1)
    h.on_frame({"type": "result", "id": 1, "success": True, "result": {"ok": True}})
    assert h.registered
    h.on_frame({"type": "device_command", "command_id": "c-1", "action": "get_battery"})
    assert h.executed == ["c-1"]


def test_a_bare_result_frame_cannot_register_the_device():
    """The regression this section exists for.

    `optInt("id", -1)` answers -1 for an ABSENT id, and a fresh session's
    registerId is also -1, so `{"type":"result","success":true}` — a frame that
    costs nothing to send and needs no token — used to satisfy
    `id == registerId` and drive the channel to READY. `device_command` was
    then accepted having never authenticated and never registered.
    """
    for bogus in [
        {"type": "result", "success": True},
        {"type": "result", "id": None, "success": True},
        {"type": "result", "id": "1", "success": True},
        {"type": "result", "id": -1, "success": True},
        {"type": "result", "id": True, "success": True},
    ]:
        h = Handshake()
        h.on_frame(bogus)
        assert not h.registered, bogus
        h.on_frame({"type": "device_command", "command_id": "c-1", "action": "sms_send"})
        assert h.executed == [], bogus


def test_a_result_before_auth_cannot_register_the_device():
    """Even a well-formed id=1 result is not an acknowledgement of nothing."""
    h = Handshake()
    h.on_frame({"type": "result", "id": 1, "success": True})
    assert not h.registered
    # …and it does not poison the real handshake that follows either.
    h.on_frame({"type": "auth_ok"})
    assert h.sent[-1] == ("register", 1)
    h.on_frame({"type": "result", "id": 1, "success": True})
    assert h.registered


def test_commands_before_registration_are_ignored():
    h = Handshake()
    h.on_frame({"type": "auth_required"})
    h.on_frame({"type": "device_command", "command_id": "c-0", "action": "sms_send"})
    h.on_frame({"type": "auth_ok"})
    h.on_frame({"type": "device_command", "command_id": "c-1", "action": "sms_send"})
    assert h.executed == []
    h.on_frame({"type": "result", "id": 1, "success": True})
    h.on_frame({"type": "device_command", "command_id": "c-2", "action": "sms_send"})
    assert h.executed == ["c-2"]


def test_a_refused_registration_never_reaches_ready():
    h = Handshake()
    h.on_frame({"type": "auth_ok"})
    h.on_frame({"type": "result", "id": 1, "success": False,
                "error": {"code": "unknown_command", "message": "no"}})
    assert not h.registered
    h.on_frame({"type": "device_command", "command_id": "c-1", "action": "get_battery"})
    assert h.executed == []


# ===========================================================================
# (e) the derived WebSocket URL — mirrored from ChannelConfig.collapseIpv6Brackets
# ===========================================================================
#
# `java.net.URI.getHost()` returns an IPv6 host WITH its brackets, and
# `ServerUrl.websocketUrl` brackets anything containing a colon — so an IPv6
# literal came out double-bracketed and unparseable, and the channel sat in
# BLOCKED forever for every IPv6 server URL.


def collapse_ipv6_brackets(url: str) -> str:
    scheme_end = url.find("://")
    if scheme_end < 0:
        return url
    start = scheme_end + 3
    slash = url.find("/", start)
    path_start = slash if slash >= 0 else len(url)
    authority = url[start:path_start]
    if "[[" not in authority:
        return url
    fixed = authority.replace("[[", "[").replace("]]", "]")
    return url[:start] + fixed + url[path_start:]


def test_a_double_bracketed_ipv6_authority_is_repaired():
    cases = [
        ("ws://[[fd00::1]]:8123/api/websocket", "ws://[fd00::1]:8123/api/websocket"),
        ("wss://[[fd00::1]]/api/websocket", "wss://[fd00::1]/api/websocket"),
        # untouched
        ("ws://192.168.2.10:8123/api/websocket", "ws://192.168.2.10:8123/api/websocket"),
        ("ws://jarvis.local:8123/proxy/api/websocket", "ws://jarvis.local:8123/proxy/api/websocket"),
        ("ws://[fd00::1]:8123/api/websocket", "ws://[fd00::1]:8123/api/websocket"),
        ("not a url", "not a url"),
    ]
    for raw, expected in cases:
        assert collapse_ipv6_brackets(raw) == expected, raw

    # …and the repaired URL is one the transport check accepts, which the
    # double-bracketed one never was.
    broken = "ws://[[fd00::1]]:8123/api/websocket"
    assert not check_url(broken), "the double-bracketed form must not be dialable"
    try:
        urlsplit(broken)
        raise AssertionError("expected the double-bracketed authority to be unparseable")
    except ValueError:
        pass
    assert check_url(collapse_ipv6_brackets(broken)), "the repaired form must be dialable"


def test_kotlin_config_repairs_the_ipv6_authority():
    src = _read(KOTLIN_CONFIG)
    assert "collapseIpv6Brackets" in src, "ChannelConfig stopped repairing the IPv6 authority"
    assert 'replace("[[", "[")' in src and 'replace("]]", "]")' in src


# ===========================================================================
# Structural checks — one copy edited without the other
# ===========================================================================


def _read(path: Path) -> str:
    assert path.is_file(), f"missing {path}"
    return path.read_text()


def test_kotlin_lanhost_still_has_every_range():
    src = _read(KOTLIN_LANHOST)
    for needle in [
        "a == 127", "a == 10", "a == 172 && b in 16..31", "a == 192 && b == 168",
        "a == 169 && b == 254", "a == 100 && b in 64..127",
        "0xfc00..0xfdff", "0xfe80..0xfebf",
    ]:
        assert needle in src, f"LanHost.kt no longer contains `{needle}`"
    assert "octal-looking" in src, "the leading-zero rejection lost its comment, and maybe its code"
    for name in sorted(LAN_CLASSES | NON_LAN_CLASSES):
        assert re.search(rf"\b{name}\b", src), f"HostClass.{name} is gone"


def test_kotlin_tierguard_has_no_way_to_lower_a_tier():
    src = _read(KOTLIN_TIERGUARD)
    assert "fun max(" in src, "TierGuard.max is gone"
    assert "ordinal >= b.ordinal" in src, "max() no longer compares severity"
    assert "incoming ?: WireTier.AUTO" in src, "a missing tier must contribute AUTO"
    assert "?: WireTier.CONFIRM" in src, "an unknown action must be CONFIRM"
    # There must be no `min`, and no function that reads a standing permission
    # or a bypass flag off the wire. (Checked against code, not prose: the file
    # is allowed to *discuss* overrides, just not to implement one.)
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith(("*", "//", "/*"))
    )
    assert not re.search(r"\bfun\s+min\b", code), "TierGuard grew a min()"
    for forbidden in ["allow_always", "skipConfirmation", "skip_confirmation", "bypass"]:
        assert forbidden not in code, f"TierGuard implements `{forbidden}`"


def test_kotlin_channel_enforces_the_pin_and_the_limits():
    src = _read(KOTLIN_CHANNEL)
    assert "followRedirects(false)" in src, "redirects are back on; the host pin is bypassable"
    assert "followSslRedirects(false)" in src
    assert "pinnedHost" in src and "host pin violated" in src, "the per-command pin check is gone"
    assert "TierGuard.forAction" in src, "the channel stopped raising tiers locally"
    assert "inbound.tryAcquire" in src, "the inbound rate limit is gone"
    assert "gate.admit" in src, "admission control is gone"
    assert "withTimeoutOrNull(cfg.commandTimeoutMs)" in src, "the hard command timeout is gone"
    assert "gate.maxConcurrent = cfg.maxConcurrentCommands" in src, \
        "the concurrency cap stopped following the configuration"


def test_kotlin_channel_only_registers_an_authenticated_socket():
    """Mirrors the Handshake class above; see test_a_bare_result_frame_…."""
    src = _read(KOTLIN_CHANNEL)
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith(("*", "//", "/*"))
    )
    # A result frame with no usable id must be rejected before anything else,
    # and the id must be an actual JSON integer — optInt() would coerce "1".
    assert 'msg.opt("id")' in code, "onResult stopped type-checking the id"
    assert 'msg.optInt("id"' not in code, \
        "onResult is back on optInt(), which coerces the string \"1\" into an id"
    assert re.search(r"if\s*\(\s*id\s*<\s*0\s*\)", code), \
        "onResult no longer refuses a result frame with no usable id"
    assert "current.registerId < 0" in code, \
        "onResult no longer requires that a register frame was actually sent"
    # Both bits gate the command path, and `registered` is only ever set on an
    # authenticated socket.
    assert "!current.authed || !current.registered" in code, \
        "device_command is no longer gated on BOTH authed and registered"
    assert re.search(r"if\s*\(\s*!current\.authed\s*\)", code), \
        "onRegistered/sendRegister no longer refuse an unauthenticated socket"
    assert code.count("registered = true") == 1, \
        "there is now more than one place that marks a session registered"


def test_kotlin_channel_keeps_the_whole_event_backlog():
    src = _read(KOTLIN_CHANNEL)
    assert "queued.subList(index, queued.size)" in src, \
        "flushEvents is back to re-queueing only the frame that failed"
    assert "addFirst" in src, "a re-queued event must go back to the FRONT of the queue"
    assert "coerceAtLeast(1)" in src, \
        "an offlineEventQueue of 0 makes removeFirst() throw on an empty deque"


def test_kotlin_lanhost_only_reads_a_dotted_tail_for_v4_mapped_prefixes():
    src = _read(KOTLIN_LANHOST)
    assert "if (isV4EmbeddingPrefix(prefix)) return classifyIpv4(" in src, \
        "LanHost is back to classifying every dotted tail as IPv4"
    assert "0xffff" in src, "the ::ffff: marker is gone"
    assert "Locale.ROOT" in src, "host normalisation is back on the device locale"


def test_kotlin_bucket_defaults_match_this_mirror():
    src = _read(KOTLIN_BUCKET)
    capacity = re.search(r"DEFAULT_CAPACITY\s*=\s*([\d.]+)", src)
    refill = re.search(r"DEFAULT_REFILL_PER_SECOND\s*=\s*([\d.]+)", src)
    assert capacity and refill, "TokenBucket lost its defaults"
    assert float(capacity.group(1)) == DEFAULT_CAPACITY
    assert float(refill.group(1)) == DEFAULT_REFILL


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"ok   {name}")
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({len(HOST_TABLE)} host cases, {len(URL_TABLE)} URL cases, "
        f"{len(TIERS) * (len(TIERS) + 1)} tier combinations)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
