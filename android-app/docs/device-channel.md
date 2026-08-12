# The device command channel

How the phone and `jarvis-core` talk, and what the phone refuses to do no
matter what the server asks.

One WebSocket to `${serverUrl}/api/websocket`, opened by the phone, kept open,
carrying commands down and results and events up. It is a **separate socket
from the voice one** in `assist/AssistPipelineClient` — same endpoint, same
handshake, deliberately different connection. Voice lives for the length of a
turn; this one lives for days and can be told to send an SMS. Sharing a socket
would mean a bug in one path could stall or confuse the other.

Implementation: `app/src/main/kotlin/ai/jarvis/app/channel/`.

| file | what | Android imports? |
|---|---|---|
| `JarvisChannel.kt` | the OkHttp client, the state machine, the dispatch path | yes |
| `DeviceLink.kt` | adapter onto the automation module's `DeviceEventSink` / `AskJarvisClient` | yes |
| `ChannelConfig.kt` | immutable per-connection config snapshot | yes |
| `NetworkWatcher.kt` | connectivity-aware retry | yes |
| `ChannelFrames.kt` | every frame, built and parsed in one place | org.json only |
| `LanHost.kt` | `isLanHost` and the cleartext rule | **no — pure** |
| `TierGuard.kt` | the tier-raise-only rule | **no — pure** |
| `TokenBucket.kt` | inbound/outbound rate limit | **no — pure** |
| `Backoff.kt` | reconnect schedule | **no — pure** |
| `CommandGate.kt` | dedupe, per-action lock, concurrency cap | **no — pure** |
| `Redact.kt` | keeps the token out of logs | **no — pure** |

The rules that matter — the cleartext classifier, the tier raise, the rate
limit, the handshake, the derived socket URL — are mirrored in Python and
actually executed:

```bash
python3 android-app/tools/channel_protocol_test.py     # 33 checks
```

---

## 1. The wire protocol

Every frame is a JSON object with a `type`. Request/response frames carry an
`id` the phone allocates; pushes (`device_command`, `device_event`) do not.

### 1.1 Handshake

The server speaks first.

```json
{"type": "auth_required", "ha_version": "jarvis-0.1.0"}
```

```json
{"type": "auth", "access_token": "eyJhbGciOi…"}
```

```json
{"type": "auth_ok", "ha_version": "jarvis-0.1.0"}
```

or

```json
{"type": "auth_invalid", "message": "invalid access token"}
```

The token appears **only** in that one frame. It is never a URL parameter, never
an `Authorization` header on the upgrade request, and never logged — see
[§5.7](#57-the-token). `auth_invalid` is treated as a settings problem, not a
network blip: the phone stops retrying quickly and says so on the settings
screen.

### 1.2 Registration

Sent immediately after `auth_ok`, and again whenever the device's capabilities
change.

```json
{
  "id": 1,
  "type": "jarvis/device/register",
  "device": {
    "id": "9f2c1e40-73aa-4f0b-8d21-6b5f0c2f9a1e",
    "name": "Pixel 8",
    "platform": "android",
    "capabilities": ["apps", "calendar", "clipboard", "device", "media", "network", "sms", "ui_automation"],
    "app_version": "1.0.0",
    "actions": [
      {
        "id": "sms_send",
        "tier": 3,
        "tier_name": "CONFIRM",
        "description": "Send an SMS to a phone number",
        "params": {"to": "E.164 phone number", "body": "message text"},
        "capability": "sms",
        "available": true,
        "delegated": false,
        "requires_confirmation": true,
        "android_permissions": ["android.permission.SEND_SMS"]
      }
    ]
  }
}
```

The five keys `id`, `name`, `platform`, `capabilities`, `app_version` are the
contract. `actions` is **additive**: it is the full action manifest, so the
server can build LLM tool definitions without a second round trip. A server that
does not know about it ignores an extra dict key. It is sent inside `device`
rather than as its own message type precisely for that reason — an unknown
message type comes back as `unknown_command`, an unknown dict key does not.

The reply:

```json
{"id": 1, "type": "result", "success": true, "result": {"ok": true}}
```

Anything else — `success: false`, an `unknown_command` error, no reply at all —
means the device is **not registered**, and an unregistered device ignores
`device_command` frames entirely. The socket is closed and the reconnect loop
backs off.

```json
{"id": 1, "type": "result", "success": false,
 "error": {"code": "unknown_command", "message": "unknown command 'jarvis/device/register'"}}
```

**What counts as an acknowledgement.** All four of these must hold before the
session is marked registered, and they are checked in this order:

1. the frame carries an `id` that is a JSON **integer** — not absent, not
   `null`, not the string `"1"`;
2. the socket has seen `auth_ok`;
3. this session actually sent a register frame (it has an allocated id);
4. the frame's `id` equals that one.

None of that is pedantry. Reading the id with a "-1 if missing" default made the
bare frame `{"type":"result","success":true}` — which costs nothing to send and
needs no token — match a fresh session's unset register id, and the channel went
straight to READY and started accepting `device_command`. On a LAN, where the
transport rule permits cleartext `ws://`, anything that can answer for the
configured host (mDNS spoofing a `.local` name, ARP, a recycled DHCP lease)
could have driven the phone without ever holding the bearer token.
`tools/channel_protocol_test.py` mirrors the state machine and asserts it.

### 1.3 Commands

Pushed by the server at any time once the device is registered.

```json
{
  "type": "device_command",
  "command_id": "c-8f31",
  "action": "sms_send",
  "params": {"to": "+441234567890", "body": "Running ten minutes late"},
  "tier": 3,
  "reason": "You asked me to tell Sam you are running late."
}
```

* `command_id` — server-issued, opaque, required. It is the dedupe key and it
  outlives any single socket.
* `action` — must exist in the device's own manifest. If it does not, it is
  treated as Tier 3, and the dispatcher will then answer `unsupported`.
* `params` — passed through **verbatim**. Nothing in here is interpreted by the
  channel. It is what the consent prompt displays.
* `tier` — advisory. Folded in as `max(local, incoming)`. See [§4](#4-tiers).
* `reason` — human-readable, shown verbatim in the consent prompt. **Untrusted
  text**: an LLM wrote it, possibly after reading a hostile web page.

Fields the phone does not have and will not grow: anything resembling
`skip_confirmation`, `policy`, `allow_always`, `trust`, `timeout_override`.

### 1.4 Results

Exactly one per accepted `command_id`, always.

```json
{"type": "device_result", "command_id": "c-8f31", "status": "ok",
 "result": {"sent": true, "parts": 1}}
```

```json
{"type": "device_result", "command_id": "c-8f31", "status": "denied",
 "error": "denied by the user"}
```

| status | meaning |
|---|---|
| `ok` | it ran, `result` holds whatever it produced |
| `denied` | policy said no, or the human said no, or the prompt timed out. **Nothing ran.** |
| `error` | it tried and failed; also the answer for a device-side timeout, a rate-limit drop, or a busy action |
| `unsupported` | this device cannot do that at all — no such action, no permission, no hardware |

There is no fifth status and no "partial". If the dispatcher returns something
with an unrecognised status, the channel rewrites it to `error`: a garbled
answer from the executor must never read as success.

### 1.5 Events

Pushed by the phone. Trigger fired, something changed.

```json
{"type": "device_event", "event": "geofence_enter", "data": {"id": "home", "at": 1754689200000}}
```

```json
{"type": "device_event", "event": "notification_posted",
 "data": {"package": "com.example.bank", "title": "Payment received", "text": "£12.40 from A. Smith"},
 "trust": "untrusted"}
```

`trust` is additive and appears only when the payload contains text somebody
else wrote — a notification body, screen content, an HTTP response. It is set
**by the trigger**, structurally, never by the payload and never by the server.
The server should treat those strings as data to show a user, not as
instructions to a model. The phone enforces the same thing on its own side
regardless of what the server does with it.

### 1.6 Heartbeat

```json
{"id": 42, "type": "ping"}
```

```json
{"id": 42, "type": "pong"}
```

Every 45 s, with a 15 s deadline for the pong. On top of that, OkHttp sends
protocol-level pings every 20 s, which catches a NAT binding that died silently
faster than the application ping does.

### 1.7 Asking the server a question

The one thing that flows the other way. It backs the `ask_jarvis` task step, via
`AskJarvisClient` in the automation module.

```json
{"id": 7, "type": "conversation/process", "text": "What should I text Sam?", "conversation_id": "01J…"}
```

```json
{"id": 7, "type": "result", "success": true,
 "result": {"response": {"speech": {"plain": {"speech": "Tell him you are ten minutes out."}}},
            "conversation_id": "01J…"}}
```

`JarvisChannel.request()` allocates the id, waits for the matching `result`, and
returns null on every failure — not connected, send failed, timed out, refused.
Only ids the phone allocated are in the pending map, so an unsolicited `result`
frame matches nothing and is ignored; the server cannot answer a question that
was never asked.

**The reply is LLM output and is treated as untrusted.** `TaskRunner` marks the
variable holding it as tainted, and any later step whose parameters mention that
variable dispatches as `TrustLevel.UNTRUSTED` — which can never be
auto-allowed. "Ask Jarvis what to say, then text it to Sam" works, and shows the
user the actual text before it goes anywhere.

---

## 2. Registration and the capability flow

```
             ┌──────────────────────────────────────────────┐
             │  ActionRegistry (automation module)          │
             │    manifest()      → every action + tier     │
             │    capabilities()  → ["sms", "media", …]     │
             └───────────────────┬──────────────────────────┘
                                 │ AutomationBridge.ActionDispatcher
                                 ▼
   auth_ok ──►  build manifest ──►  jarvis/device/register  ──►  result{ok}
                       │                                             │
                       ▼                                             ▼
              tierTable: id → tier                              state = READY
              (the channel's own local table)              commands are accepted
```

Two things come out of the manifest and both matter:

1. **`capabilities`** — coarse strings the server uses to decide which tools to
   offer the model. Only actions that are `available` *right now* contribute, so
   a phone with no SMS radio does not advertise `sms` and the model never plans
   around it. One entry does not come from the manifest: `ui_automation` is
   added by the channel itself, from `AutomationBridge.uiAutomation`, and only
   while the accessibility service is enabled **and connected**. It rides in
   this list rather than in a field of its own precisely because this list has
   consumers end to end — the slot spent its whole life filled by the
   accessibility service and read by nothing, which is what
   `tools/no_empty_seams_test.py` exists to catch.
2. **`tierTable`** — `action id → tier`, kept by the channel. This is the
   channel's own copy of the local tier table, built from the device's own
   manifest and never from anything the server sent. It is what lets a
   `device_command` be tier-checked on the socket thread, before it reaches an
   executor.

### Re-registering

The capability list is a promise about what the phone can do. It goes stale the
moment the user toggles the accessibility service, grants notification access,
answers a runtime permission dialog, or Shizuku binds. Whoever notices calls:

```kotlin
AutomationBridge.onCapabilitiesChanged()
```

which re-sends `jarvis/device/register` on the live socket. No reconnect, no
gap in command handling.

### Device identity

`device.id` is a random UUID generated on first run and kept in
`SharedPreferences`, via `JarvisConfig.deviceId`. Deliberately **not** derived
from `ANDROID_ID`, the IMEI, a MAC address or a serial number:

* a hardware id survives a factory reset, which makes it a tracking identifier;
* a hardware id is shared with anything else that can read it;
* an id that survives a reinstall would let a fresh install silently inherit an
  old install's authorisation on the server.

Reinstalling the app produces a new device that has to be authorised again.
That is the intended cost.

---

## 3. Connection lifecycle

```
 STOPPED ──start()──► [config?] ──no──► BLOCKED ──30 s──┐
                          │                            │
                         yes                           │
                          ▼                            │
                   [transport ok?] ──no──► BLOCKED ────┤
                          │                            │
                         yes                           │
                          ▼                            │
                    [network?] ──no──► OFFLINE ────────┤
                          │            (park until a network appears)
                         yes                           │
                          ▼                            │
                     CONNECTING                        │
                          ▼                            │
                   AUTHENTICATING                      │
                          ▼                            │
                     REGISTERING                       │
                          ▼                            │
                       READY  ──socket lost──► BACKING_OFF ──┘
```

### Backoff

`Backoff.kt`: uniform random in `[1 s, min(5 min, 1 s × 2^attempt)]`.

* The **floor** is 1 s. Textbook full jitter draws from `[0, cap]`, and a
  zero-length delay against a server that is refusing the handshake is a hot
  loop with extra steps.
* The **ceiling** is 5 min.
* The jitter is not decoration. A fixed reconnect cadence is a beacon — anyone
  watching the WireGuard link can fingerprint "this is a Jarvis phone" from the
  timing alone — and it also puts every client back on a restarting server at
  the same instant.
* `attempt` resets to zero **only on a successful registration**. Not on a
  successful TCP connect, not on `auth_ok`. A server that accepts sockets and
  then fails registration is still broken, and the backoff should reflect that.
* Some failures skip straight to a ~64 s floor (`penalise()`), because they will
  not fix themselves in a second: a rejected token, HTTP 401/403 on the upgrade,
  a redirect to a different host, a transport policy refusal.

### Connectivity-aware retry

`NetworkWatcher` registers a default-network callback (`ConnectivityManager`,
no Play Services). Two effects:

* the loop **parks** rather than retrying into an aeroplane-mode radio;
* a network appearing **cuts the current backoff short**, so a Wi-Fi handover
  reconnects immediately instead of finishing a five-minute sleep that started
  in a lift.

It deliberately does **not** require `NET_CAPABILITY_VALIDATED`. That flag means
"Android reached a captive-portal probe on the internet", and the entire point
of this app is a box on your LAN. A home network with the WAN unplugged is
unvalidated and perfectly usable. If connectivity monitoring cannot be set up at
all, the watcher **fails open** and assumes there is a network — failing closed
there would mean "no monitoring" silently equals "no Jarvis".

### Concurrency and back-pressure

| limit | value | on breach |
|---|---|---|
| inbound commands | 10 burst, 1/s sustained (`TokenBucket`) | dropped, warning logged, `error` returned with a retry hint |
| one in-flight per action id | — | `error`: "already running on this device" |
| global in-flight | 4 | `error`: "already running N commands" |
| duplicate `command_id` | — | the stored reply is replayed; **nothing re-executes** |
| hard timeout per command | 180 s | `error`, and the dispatch coroutine is cancelled |
| inbound frame size | 512 KiB | dropped with a warning |
| offline event queue | 64, oldest dropped | warning logged |

The 180 s timeout is generous on purpose. A Tier-3 command spends up to 60 s on
the consent screen before its action even starts. This is a watchdog for a
dispatcher that never returns at all, not a performance budget — and on expiry
the server gets `error`, never silence.

### Exactly-once, across reconnects

Losing the socket does **not** cancel a running command; the coroutine lives in
the channel's scope and may be sitting on a consent prompt the user is reading
right now. So:

* in-flight bookkeeping survives a reconnect. A redelivered `command_id` that is
  still running is answered once, by the original run.
* the reply is written to the dedupe history **before** it is sent. If the send
  fails because the socket just died, the redelivery after the reconnect is
  answered from cache — the SMS is not sent twice.
* the reply goes out on whatever socket is live at the time, not the one the
  command arrived on.
* an explicit `stop()` cancels everything, which cancels the pending consent
  prompts too. Those commands were never answered, so a redelivery after the
  next `start()` is allowed to run from scratch.

### Clean shutdown

`stop()` closes the socket with 1000, cancels the scope (and with it every
in-flight command and its consent prompt), unregisters the network callback,
clears the queues, and releases the `AutomationBridge` slot. It is idempotent,
and `start()` after `stop()` works — the scope is rebuilt.

---

## 4. Tiers

The rule, in full:

```
effective = max(localTier, incomingTier ?: AUTO)
```

and it is applied **twice, independently**:

1. In `JarvisChannel`, against `tierTable` — the manifest the device itself
   produced. An action absent from that table is `CONFIRM`.
2. In `ActionRegistry`/`PolicyEngine`, against the real local action table,
   which is the authority. See `docs/actions.md`.

Neither one can lower anything. `TierGuard.kt` contains no `min`, no override
flag, and no function that reads a standing permission off the wire — there is
no code path to audit because there is no code.

A malformed, absent or hostile `tier` value parses to `null`, which contributes
`AUTO` to the `max` and therefore changes nothing. So a hostile value has
exactly two possible outcomes: raise the tier, or do nothing.

```
server says tier 1 for sms_send  →  local table says CONFIRM  →  CONFIRM
server says tier 3 for get_battery →  local table says AUTO   →  CONFIRM
server says tier 1 for not_an_action → not in the table       →  CONFIRM
```

A downgrade attempt is logged as a warning. It is the single most interesting
line in the log, because a well-behaved server never produces one.

---

## 5. Threat model: a hostile or prompt-injected server

The LLM runs on the server. It reads web pages, notifications and screen text.
Therefore **the server is not trusted with anything dangerous**, and this
section is the list of things the phone refuses to do regardless of what arrives
on the socket. Everything here is enforced in code on the device, outside the
model.

The starting assumption is the worst realistic one: the server is fully
controlled by an attacker — either because a page it read injected it, or
because the box itself is compromised. What can it get?

### 5.1 It cannot lower a tier

Covered above. The strongest thing a hostile `tier` field can do is make an
action *more* restricted.

### 5.2 It cannot make the phone approve anything

Approval is a full-screen prompt on the device showing the verbatim action,
parameters and reason. The channel has no code path that sets, skips, shortens
or remembers an approval, and the frame has no field that would carry one.
Tier 3 is asked every time and is never remembered — see `docs/actions.md` and
the `ApprovalBridge` contract in the app README.

Denied, timed out, prompt undeliverable, process killed mid-prompt: all produce
`status: "denied"`, and the action's `execute()` is never called.

### 5.3 It cannot move the socket somewhere else

* The URL is built from local settings. The server never supplies it.
* HTTP redirects are **off** (`followRedirects(false)`, `followSslRedirects(false)`),
  so a 30x on the upgrade fails the handshake rather than relocating the socket.
* At open, the final request's host is compared against the configured host.
* On **every** `device_command`, the socket's host is compared again. That check
  runs per command rather than once at connect, because a check that runs once
  is a check that quietly stops running the day somebody adds another reconnect
  path around it.

A mismatch closes the socket and takes the long backoff.

Hosts are compared as normalised strings, not as resolved addresses. That is
fail-closed and has one visible cost: an IPv6 literal written long-hand in
Settings (`http://[fd00:0:0:0:0:0:0:1]:8123`) will not match OkHttp's compressed
form, and the channel will refuse to run commands — loudly, with both values in
the log. Write the compressed form (`[fd00::1]`) or use a name. Refusing a
legitimate server costs one edit; accepting a host nobody configured does not
have a cheap fix.

### 5.4 It cannot downgrade the transport

`LanHost.checkUrl` runs before the socket opens:

* `https://` / `wss://` — allowed to any host.
* `http://` / `ws://` — allowed **only** to loopback, RFC1918 (`10/8`,
  `172.16/12`, `192.168/16`), link-local (`169.254/16`), CGNAT/`100.64/10`
  (Tailscale and other WireGuard meshes), IPv6 ULA `fc00::/7`, IPv6 link-local
  `fe80::/10`, or a name that only a local resolver answers (`*.local`, `*.lan`,
  `*.home.arpa`, `*.internal`, a single label with no dot).
* anything else — refused, with a message naming the host.

Ambiguity resolves to "public", because classifying a host as LAN is what
*permits* cleartext. `0177.0.0.1` is `127.0.0.1` to `inet_aton` and `177.0.0.1`
to a naive parser, so leading-zero octets are rejected outright: a classifier
that can be made to disagree with the resolver is a bypass waiting to happen.
`1::` is not loopback despite reducing to `1`. `fec0::/10` (deprecated
site-local) is not private space.

A dotted quad on the end of an IPv6 address is read as an IPv4 address **only**
under the two prefixes where it is one: `::a.b.c.d` and `::ffff:a.b.c.d`, in the
compressed or the long-hand `0:0:0:0:0:ffff:` spelling. Anywhere else those are
simply the low 32 bits of an ordinary address, and classifying by them was a
cleartext bypass: `2001:4860:4860::10.0.0.1` is globally routable, but its tail
reads `10.0.0.1`, so `http://[2001:4860:4860::10.0.0.1]:8123` classified as
RFC1918 and the bearer token would have gone out in the clear.
`fd00::192.168.1.1` is still a ULA and still private, because the *address* is,
not because the tail says so.

IPv6 literals also survive the trip to the socket URL now. `java.net.URI`
reports an IPv6 host with its brackets and `ServerUrl.websocketUrl` brackets
anything containing a colon, so `http://[fd00::1]:8123` used to derive
`ws://[[fd00::1]]:8123/api/websocket` — unparseable, refused by the transport
check, and the channel sat in BLOCKED forever. `ChannelConfig` collapses the
duplicate brackets in the authority before the URL is used.

The one escape hatch is a per-host acknowledgement the **user** types on the
device — the `channel_cleartext_ack` key in the `jarvis_config` prefs, read by
`ChannelConfig.from`. Nothing on the network can add to that list, and nothing
in the app writes it yet: no settings screen exposes it, so today the list is
always empty and the rule above has no exception at all. If a settings screen
ever grows the field, it is the only thing that may write that key.

There is a second, independent layer: `res/xml/network_security_config.xml`
denies cleartext by default and permits it only for hosts listed there. Both
have to agree before a plain-HTTP byte leaves the phone.

### 5.5 It cannot flood the user into approving something

Consent fatigue is a real attack: a wall of prompts is a wall nobody reads, and
a wall nobody reads is a wall somebody taps through. So arrival rate is bounded
*before* policy is consulted — 10 burst, 1/s sustained. Over-rate commands are
never dispatched; they are answered with `error` and a retry hint and logged as
a warning. (Answered rather than silently dropped, so the server does not hang
waiting for a `device_result` that will never come.)

The per-action lock and the global cap bound it further: no more than one
`ui_type` at a time, no more than four consent prompts alive at once. The cap is
`ChannelConfig.maxConcurrentCommands` (4), pushed into the gate on every
reconnect — the gate outlives a socket, because that is what keeps a redelivered
`sms_send` from being sent twice, so the cap is set on it rather than baked in
at construction.

### 5.6 It cannot make the phone act on text it fetched

This is the prompt-injection case proper, and the structural answer is that
untrusted content and the action dispatcher are never connected by a code path
that lacks a human.

* Content from web pages, notifications, the clipboard and screen reads is
  marked `TrustLevel.UNTRUSTED` **by its source**, structurally. No field in a
  payload can raise its own trust, and neither can the server.
* An untrusted request can never be auto-allowed. `PolicyEngine` degrades
  `ALLOW` to `ASK` for it, so the strongest outcome injected text can ever
  reach is a fresh human approval showing the real parameters.
* The channel never parses a `reason` string for a decision. It is displayed and
  logged; it is never read.
* `device_event` payloads flow *outward*. Publishing an event cannot dispatch
  an action: subscribers either serialise it onto the socket or hand it to the
  task runner, and the task runner dispatches through the same policy door as
  everyone else, with the trust level attached.

### 5.7 The token

* Transmitted in exactly one frame, `auth`. Never in a URL, never in a header on
  the upgrade request, so it does not land in a reverse-proxy access log.
* Never logged. `Redact.token()` renders it as `eyJh…(214 chars)` on the one
  line that has to identify *which* token, and `Redact.text()` strips
  `access_token` / `token` / `authorization` / `password` / `api_key` out of any
  string on its way to a log — including exception messages from OkHttp.
* Raw frames are not logged at all. `Redact.frame()` exists for deliberate
  debugging, not for the hot path.
* `logcat` is readable by anything holding `READ_LOGS`, by anyone with the phone
  unlocked and a cable, and by a bug report the user emails to somebody. "We
  never log it" is a property of today's code; `Redact` is a property of the
  code after the next edit.

### 5.8 What it *can* do

Being honest about the residual risk:

* **Ask for anything, endlessly** (within the rate limit). Tier 1 actions run
  without asking, by design: reading battery level, screen state, coarse
  location, media state. A hostile server learns those.
* **Word the consent prompt.** `reason` is attacker-controlled text on a screen
  the user is about to make a decision on. Mitigation is presentational and
  lives in `ApprovalActivity`: the action's *local* description and the
  *server's* reason are two separately labelled fields, the parameters shown are
  the ones about to execute, and there is a RAW toggle because pretty-printed
  JSON can hide a duplicate key.
* **Deny you service.** It can simply not send commands. There is no defence
  against your own server being down, and none is attempted.
* **See the manifest and the events you send it.** That is what it is for. If
  the server is compromised, the event stream — geofences, notifications you
  chose to forward — is compromised with it.

What it cannot do is send an SMS, place a call, type into an app, run a shell
command, delete a file or install a package without a human looking at the real
parameters and tapping approve.

---

## 6. Wiring

`AutomationBridge` (`automation/AutomationBridge.kt`) is where the modules meet.
It holds interfaces and volatile slots; it decides nothing.

| interface | implemented by | consumed by |
|---|---|---|
| `ActionDispatcher` | actions module — thin adapter over `ActionRegistry` | channel, task runner |
| `ChannelHandle` | **this module** — `JarvisChannel` | triggers, task runner, settings UI |
| `UiAutomationStatus` | accessibility module | channel, for the capability list |
| `DeviceEventSubscriber` | **this module** and the task runner | `AutomationBridge.publishEvent` |

The adapter the actions module registers is written out in full in the KDoc at
the top of `AutomationBridge.kt`. The short version:

```kotlin
AutomationBridge.dispatcher = object : AutomationBridge.ActionDispatcher {
    override fun manifest(): JSONArray = registry.manifest()
    override fun capabilities(): List<String> = registry.capabilities()
    override suspend fun dispatch(
        actionId: String, params: JSONObject, tier: String, reason: String
    ): JSONObject = /* registry.dispatch(...) → {"status": …} */
}
```

There is a second seam, older than `AutomationBridge` and owned by the
automation module: `AutomationRuntime.deviceEvents` (`DeviceEventSink`) and
`AutomationRuntime.askJarvis` (`AskJarvisClient`), both defaulting to no-ops so
the phone runs its automations with no server attached at all. `DeviceLink`
fills both by delegating to the channel, and derives the `trust` marker from
`TriggerIds.trustFor(event)` so an untrusted payload stays labelled even though
that interface has no trust parameter.

**Wire one event path, not two.** Starting the channel, from the automation
foreground service:

```kotlin
val channel = JarvisChannel(
    context = this,
    configProvider = { ChannelConfig.from(this, BuildConfig.VERSION_NAME) }
)
val link = DeviceLink(channel)
AutomationRuntime.deviceEvents = link
AutomationRuntime.askJarvis = link

channel.start(subscribeToBridgeEvents = false)   // ← events arrive via DeviceLink
```

Plain `channel.start()` also subscribes the channel to
`AutomationBridge.publishEvent`. Doing both sends every event to the server
twice, which is why the flag exists.

`configProvider` is re-read on every reconnect, so editing the server URL in
Settings takes effect on the next attempt without restarting the service. A live
socket keeps the snapshot it was opened with — a host pin that can change under
a command being validated is not a pin.

Sending an event from a trigger:

```kotlin
AutomationBridge.publishEvent(
    event = TriggerIds.GEOFENCE_ENTER,
    data = JSONObject().put("id", "home"),
    untrusted = false
)
```

Observing the connection from the settings screen:

```kotlin
channel.status          // StateFlow<JarvisChannel.Status> — state, host, action count, last error
channel.describe()      // "ready · 192.168.2.10 · 47 actions"
```

`Status` contains no secret and is safe to render.

---

## 7. Tests

```bash
python3 android-app/tools/channel_protocol_test.py          # 33 checks, no network
python3 -m pytest android-app/tools/channel_protocol_test.py -q
```

It mirrors and executes the pure rules — `isLanHost` and the cleartext policy
(63 host cases, 22 URL cases), the tier-raise-only rule (exhaustive over every
action × every claimed tier, including malformed ones), the token bucket (burst,
sustained rate, fractional refill, a backwards clock), the handshake state
machine that decides when a session may accept a `device_command`, and the IPv6
authority repair — and then greps the Kotlin to catch one copy being edited
without the other: the IP ranges and the `::ffff:` marker in `LanHost.kt`, the
absence of a `min` in `TierGuard.kt`, and the presence in `JarvisChannel.kt` of
`followRedirects(false)`, the per-command host pin, the tier raise, the
admission gate, the configured concurrency cap, the hard timeout, the
`authed && registered` command gate and the whole-backlog event flush.

The greps are mutation-checked: breaking each invariant in a scratch copy of the
Kotlin makes the corresponding assertion fail.

Not covered here, and worth instrumented tests when a device is available:
the reconnect state machine under real socket loss, and the interaction between
a cancelled command and a consent prompt already on screen.
