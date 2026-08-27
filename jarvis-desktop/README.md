# jarvis-desktop

The Jarvis device agent for Linux, macOS and Windows. It connects out to
jarvis-core over the same WebSocket the phone app uses, advertises what this
machine can do, and executes what the server asks — subject to a policy this
machine enforces itself.

The point of that last clause is the whole reason this program exists. The LLM
runs on the server. The server can be wrong, and it reads web pages, so it can
be lied to. **The device decides what is allowed to happen to the device**, in
plain code, outside the model.

```
jarvis-core  ──ws──►  jarvis-desktop
                        ├─ local tier table   (the authority)
                        ├─ user policy store  (allow_always | ask | never)
                        ├─ consent prompt     (Tier 3: every time, verbatim)
                        ├─ action             (path guard, SSRF guard, denylist)
                        └─ audit log          (JSONL, redacted, yours to read)
```

---

## Install

Python 3.11 or newer. The only hard dependency is the WebSocket client.

```bash
cd jarvis-desktop
python3 -m pip install -e .
```

Optional extras, none of them required:

| extra | what it adds | without it |
|---|---|---|
| `metrics` (psutil) | CPU %, memory, battery detail | stdlib fallbacks (`/proc`, `sysconf`, sysfs) |
| `clipboard` (pyperclip) | cross-platform clipboard | `wl-copy`/`xclip`/`xsel`/`pbcopy`/PowerShell, else unsupported |
| `input` (pyautogui) | type/click/move/screenshot | those four report `unsupported` with an install hint |
| `windows` (win10toast) | Windows toast notifications | PowerShell, else a log line |

```bash
python3 -m pip install -e '.[metrics,clipboard]'      # a sensible default
python3 -m pip install -e '.[full]'                   # everything
```

On Linux the consent dialog wants `python3-tk`. Without it the agent falls back
to a terminal prompt, and without a terminal it denies. It never falls open.

A denial nobody could have answered is not silent. When there is no display and
no TTY — a `systemd --user` unit inherits neither — the refusal goes out as a
desktop notification (`notify-send` / `osascript` / a Windows toast), naming the
action and the tier and pointing at the audit log for the rest. It carries
neither the parameters nor the server's stated reason: a toast can be shown on a
lock screen. `--headless` is the one case that stays quiet, because there the
operator has already said nobody is watching.

The terminal prompt answers one question at a time, through a single reader
thread that lives as long as the process. `readline()` cannot be interrupted, so
a prompt nobody answers leaves a read outstanding — but the next line typed goes
to whichever prompt is on screen when it arrives, so one unanswered question no
longer disables terminal approval until a restart. A line that arrives with no
prompt waiting is discarded rather than banked, and a timeout is still a denial.

Check what this machine can actually do:

```bash
python3 -m jarvis_desktop doctor
```

## Run

```bash
export JARVIS_TOKEN=$(cat ~/.config/jarvis/token)
python3 -m jarvis_desktop run --server ws://jarvis.lan:8080
```

`run` is the default subcommand, so `python -m jarvis_desktop --server ...`
works too. The server URL accepts `host`, `host:8080`, `http://host` or a full
`ws://host/api/websocket` — it is normalised to the WebSocket endpoint either
way.

### Is it running?

```bash
python3 -m jarvis_desktop status            # once
python3 -m jarvis_desktop status --watch    # redraws until ^C
python3 -m jarvis_desktop status --json     # for a script
```

```
jarvis-desktop 0.1.0: running, connected to the server
  device      workshop-desktop (desktop-19efc0e2cf33)
  server      ws://jarvis.lan:8080/api/websocket
  pid         41207 (alive)
  uptime      2h 14m
  updated     1s ago
  consent     tk-dialog
  actions     21

the last 3 action(s), newest first:
     14:02:11  read_file            AUTO     ok
  !! 14:01:58  run_command          CONFIRM  denied
```

The running agent rewrites `status.json` in the state directory every few
seconds and `status` reads it; the recent actions come from the audit log, which
already records every dispatch. There is no tray icon and no listening socket —
one file is the whole mechanism, and it adds no dependency.

A status file is a claim that an agent was alive when it was written, not that
one is running now. A clean shutdown removes it; a killed agent cannot, so a
file nobody has refreshed reads as `STALE`, and one whose pid is gone reads as
`NOT RUNNING`. `status` exits non-zero in both cases, and when there is no file
at all, so it can be used as a health check.

`doctor` answers a different question — what this *machine* can do — and works
with no agent running at all.

### Configuration

Precedence, lowest to highest: defaults → config file → environment → flags.
The config file lives at `~/.config/jarvis-desktop/config.json`
(`~/Library/Application Support/…` on macOS, `%APPDATA%\…` on Windows):

```json
{
  "server_url": "ws://jarvis.lan:8080/api/websocket",
  "pinned_host": "jarvis.lan",
  "device_name": "workshop-desktop",
  "file_roots": ["~/jarvis-workspace", "~/Documents/notes"],
  "clipboard_enabled": true,
  "notifications_enabled": true,
  "consent_timeout_s": 60,
  "shell": {
    "enabled": true,
    "use_shell": false,
    "timeout_s": 30,
    "max_output_bytes": 65536,
    "extra_denylist": ["\\bterraform\\s+destroy\\b"],
    "env_passthrough": ["EDITOR"]
  },
  "input_automation": { "enabled": false, "screenshot_dir": "screenshots" },
  "triggers": [
    { "type": "schedule", "id": "nightly", "cron": "0 3 * * *" },
    { "type": "file", "id": "inbox", "path": "~/jarvis-workspace/inbox", "interval_s": 10 },
    { "type": "idle", "id": "away", "threshold_s": 600 }
  ]
}
```

`allow_plaintext_ws` defaults to true, because the intended deployment is a LAN
or a WireGuard link. Set it to false and the agent refuses to dial `ws://` at
all, which is what you want the moment the server is reachable over anything
less private.

Environment variables: `JARVIS_SERVER`, `JARVIS_TOKEN`, `JARVIS_TOKEN_FILE`,
`JARVIS_STATE_DIR`, `JARVIS_FILE_ROOTS`, `JARVIS_DEVICE_NAME`,
`JARVIS_PINNED_HOST`, `JARVIS_SHELL_ENABLED`, `JARVIS_INPUT_ENABLED`,
`JARVIS_HEADLESS_DENY`.

Prefer `JARVIS_TOKEN_FILE` to `JARVIS_TOKEN`: environment variables show up in
`/proc/<pid>/environ` and in systemd's `show` output; a `0600` file does not.

State — the policy store, the audit log and the device identity — lives in
`$XDG_STATE_HOME/jarvis-desktop` (`~/.local/state/jarvis-desktop` by default),
created `0700`.

---

## The security model

Every action carries a tier. The tier decides whether a human is asked.

| tier | meaning | examples |
|---|---|---|
| **1 AUTO** | read-only or trivially reversible; runs without asking | `get_system_state`, `read_file`, `list_dir`, `list_windows`, `launch_app` (no arguments), `open_url`, `notify`, `set_volume` |
| **2 NOTIFY** | changes state but is recoverable; asks once, then may be remembered | `write_file`, `read_clipboard`, `write_clipboard`, `http_request` (GET), `screenshot`, `focus_window` |
| **3 CONFIRM** | asks **every single time**, showing the verbatim action, parameters and reason | `run_command`, `delete_file`, `type_text`, `click`, `move_mouse`, `lock_screen`, `sleep`, `http_request` (POST/PUT/PATCH/DELETE), `launch_app` (with arguments) |

`python3 -m jarvis_desktop tiers` prints the base tier for each action on your
machine, along with your standing answer. Two of them raise themselves from the
parameters, so the table is a floor rather than the whole story:

* `http_request` is Tier 2 for GET/HEAD and Tier 3 for anything that writes.
* `launch_app` is Tier 1 to *open* an app and Tier 3 to hand one a command line.
  Opening Firefox is not the same act as `{"app": "sh", "args": ["-c", "..."]}`,
  and if arguments were free then Tier 1 would be worth exactly as much as the
  Tier 3 shell gate. Interpreters, privilege tools and power commands (`sh`,
  `python3`, `sudo`, `poweroff`, `rundll32`, ...) are refused as app names
  outright, with or without arguments — `run_command` is where those belong.

### The rules that hold in code

1. **Tier is decided locally.** The `tier` field on an incoming `device_command`
   is a hint from a machine that may have been prompt-injected. It is folded in
   only through `max(local, incoming)`, so it can *raise* a tier and can never
   lower one. A server that labels an `rm -rf` as Tier 1 gets a Tier 3 prompt
   anyway. A garbage value contributes nothing.
2. **An unknown action is Tier 3.** Not "unknown", not "ask the server" — the
   most dangerous tier there is, so a typo or an injected action name cannot
   land in the auto-run bucket. (It is also refused as `unsupported`, but the
   tiering matters if one is ever added.)
3. **Tier 3 can never be auto-approved and can never be remembered.**
   `allow_always` is ignored for CONFIRM by the engine, refused by the policy
   store, and not even offered by the prompt. Three independent guards, because
   this is the invariant everything else rests on.
4. **`never` wins over everything** — over the server, over an `allow_always`
   set earlier, over the tier.
5. **Denied, timed out, or no way to ask ⇒ nothing executes** and the server is
   told `denied`. There is no code path that returns approval without a human
   having typed or clicked something.
6. **Approval is consent to run now, not a licence.** The policy store is
   re-read after the prompt returns, so hitting panic *while the prompt is on
   screen* still stops the action.
7. **Everything executed is written to a local audit log** you can read with
   `tail -f`.
8. **Fetched content is data, not instructions.** File contents, clipboard,
   command output, window titles, HTTP bodies and screenshots all come back
   flagged `_untrusted`. There is no code path that takes text out of one of
   those and puts it into the action dispatcher. If the server reads a poisoned
   README and decides to act on it, that decision arrives as a *new*
   `device_command` and gets the full treatment — tier, policy, prompt.

Trigger wiring makes point 8 structural rather than a rule someone has to
remember: `TriggerManager` is handed a single `emit` callback that reaches the
WebSocket and nothing else. There is no reference to the action registry in
`triggers.py`, so a watched file changing cannot cause anything to run here.

### Kill switches

```bash
python3 -m jarvis_desktop policy panic on     # deny everything, now
python3 -m jarvis_desktop policy disable      # master switch off
python3 -m jarvis_desktop policy list         # what is remembered
```

Panic outranks the master switch, every remembered `allow_always` and every
incoming command. Only a human clears it. Both are read fresh on every decision,
so `policy panic on` in another terminal takes effect on the next command — and
on any prompt currently waiting for an answer.

---

## Granting more or less power

The agent starts deliberately narrow. Widening it is always a local config edit;
**the server cannot widen anything**. There is no action that writes to the
config, the policy store or the file roots list.

### Less

```bash
python3 -m jarvis_desktop policy set run_command never   # blocked outright
python3 -m jarvis_desktop policy set read_clipboard never
```

```json
{ "shell": { "enabled": false }, "clipboard_enabled": false }
```

A capability turned off here is not advertised to the server at all, so the
model is never offered the tool.

Run it headless — for a server with no human at the console — with
`--headless` or `JARVIS_HEADLESS_DENY=1`. Everything that needs a prompt is
refused immediately rather than waiting a minute first, and the refusal says so
rather than claiming a user denied it.

### More

```bash
python3 -m jarvis_desktop policy set write_file allow_always   # stop asking
```

Only Tier 1 and Tier 2 can be remembered. The CLI refuses `allow_always` on a
Tier 3 action and explains why.

```json
{
  "file_roots": ["~/jarvis-workspace", "~/Documents/notes"],
  "input_automation": { "enabled": true },
  "shell": { "use_shell": true }
}
```

Two of those deserve a pause:

* **`input_automation.enabled`** lets the agent type and click into whatever is
  focused, and it cannot see what that is. Every one of those actions is Tier 3,
  so you approve each with the text in front of you — but a machine that never
  needs it should leave this off.
* **`shell.use_shell`** turns on `shell=True`. Without it a command is split
  with `shlex` and exec'd, so `;`, `&&`, backticks, `$(…)` and redirection are
  literal argument text. With it they are syntax. Turn it on only if you
  actually need pipelines.

### The file sandbox

Every file action is confined to `file_roots` (default `~/jarvis-workspace`).
Relative paths resolve against the first root; an absolute path is accepted only
if it is already inside one. Three layers, all applied:

1. syntactic rejection — null bytes, `~`, percent-encoded separators, URL
   schemes, UNC paths, backslashes on POSIX;
2. arithmetic traversal — `..` pops a segment, popping past the root is a
   rejection, decided before anything touches the disk;
3. realpath containment — the fully resolved path, symlinks and all, must still
   be inside a root.

Layer 3 is the one that matters: a symlink planted in the workspace pointing at
`/etc/shadow` is visible in `list_dir` (flagged `escapes_workspace`) and
unreadable by `read_file`. A recursive delete never follows a symlink out.

### The shell denylist

`run_command` refuses `rm -rf /`, `mkfs`, `dd of=/dev/…`, `shutdown`, the fork
bomb, `curl | sh`, `vssadmin delete shadows` and a dozen similar shapes, before
the prompt is even shown. Add your own with `shell.extra_denylist`.

**This is a tripwire, not a sandbox.** A denylist over a Turing-complete
interpreter can always be evaded, and pretending otherwise would be the
dangerous part. The real boundary is that `run_command` is Tier 3: you see the
command. The denylist is there to catch a confident model doing something
catastrophic, not to contain an attacker who already has your approval to run
commands.

The child process also gets a scrubbed environment: a small allowlist, minus
anything whose name looks like a credential, minus every `JARVIS_*` variable.
`JARVIS_TOKEN` never reaches a command the model wrote.

### The SSRF guard

`http_request` is for public pages and public APIs. It refuses loopback,
RFC1918, link-local (including `169.254.169.254`), CGNAT, multicast and the
cloud-metadata names — in every spelling a libc resolver accepts, so
`http://2130706433/`, `http://0177.0.0.1/` and `http://[::ffff:127.0.0.1]/` are
all blocked. Hostnames are resolved and **every** returned address is re-checked
before the socket opens, and every redirect hop is checked again.

A credential does not travel across a redirect: if a hop changes scheme, host or
port, `Authorization` and every other credential-shaped header is dropped before
the next request goes out. An open redirect on a site you hold a token for is
not a way to hand that token to somebody else.

The single exemption is the configured jarvis-core host: it is the machine we
already talk to over an authenticated socket.

One honest limit: the guard resolves the name, checks every address, and then
`urllib` resolves it again to open the socket. A name whose DNS answer changes
between those two moments (classic DNS rebinding) is checked on the first answer
and connected on the second. Closing that needs pinning the socket to the
address that was checked, which `urllib` does not expose. The blast radius is
bounded by the tier — `http_request` is Tier 2, and its body comes back flagged
untrusted either way.

---

## Triggers

Triggers emit `device_event` frames. They never run actions.

```json
"triggers": [
  { "type": "schedule", "id": "nightly", "cron": "0 3 * * *" },
  { "type": "file", "id": "inbox", "path": "~/jarvis-workspace/inbox",
    "interval_s": 10, "recursive": true },
  { "type": "idle", "id": "away", "threshold_s": 600, "interval_s": 30 },
  { "type": "manual", "id": "poke" }
]
```

Cron is the usual five fields plus `@daily`-style aliases, `*/n`, ranges, lists
and three-letter month/day names. Preview one before trusting it:

```bash
python3 -m jarvis_desktop cron "*/15 9-17 * * mon-fri" --count 5
```

Day-of-month and day-of-week follow Vixie cron: when *both* are restricted, a
day matches if *either* does.

File watching is polling, on purpose — no third-party watcher, identical on
every OS, and the failure mode (missing a change that was reverted between
polls) is obvious rather than subtle. The event carries the path, size and
mtime; **never the file's contents.** Reading it is an action, and actions go
through policy.

Idle detection uses `xprintidle` on X11, `ioreg` on macOS and
`GetLastInputInfo` on Windows. A Wayland session usually exposes nothing, and
the trigger reports itself inert rather than guessing.

---

## Running it as a service

### Linux — systemd user unit

`~/.config/systemd/user/jarvis-desktop.service`:

```ini
[Unit]
Description=Jarvis desktop agent
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
Environment=JARVIS_SERVER=ws://jarvis.lan:8080
Environment=JARVIS_TOKEN_FILE=%h/.config/jarvis/token
ExecStart=%h/.local/bin/jarvis-desktop run
Restart=on-failure
RestartSec=10

# It talks to your session, so it is not sandboxed away from it — but it has no
# business gaining privileges or writing outside your home.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-write
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=graphical-session.target
```

```bash
chmod 600 ~/.config/jarvis/token
systemctl --user daemon-reload
systemctl --user enable --now jarvis-desktop
journalctl --user -u jarvis-desktop -f
```

A **user** unit, not a system one: the agent needs your session bus, your
display and your clipboard, and it should have exactly your privileges and not
one more. Do not run it as root — nothing it does needs root, and the consent
prompt is worth much less if approving it hands over the machine.

For a headless box, add `Environment=JARVIS_HEADLESS_DENY=1` and drop the
`graphical-session` bits. Tier 2 and Tier 3 actions will be refused, which is
the honest outcome when nobody can be asked.

### macOS — launchd

`~/Library/LaunchAgents/ai.jarvis.desktop.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>ai.jarvis.desktop</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/jarvis-desktop</string>
    <string>run</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>JARVIS_SERVER</key>     <string>ws://jarvis.lan:8080</string>
    <key>JARVIS_TOKEN_FILE</key> <string>/Users/you/.config/jarvis/token</string>
  </dict>
  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <true/>
  <key>StandardErrorPath</key><string>/tmp/jarvis-desktop.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/ai.jarvis.desktop.plist
```

A LaunchAgent, not a LaunchDaemon — same reasoning as the systemd user unit.
macOS will ask for Accessibility and Screen Recording permission the first time
input automation or a screenshot is used; those prompts are the OS's, separate
from and additional to Jarvis's own.

### Windows — Task Scheduler

```powershell
$action  = New-ScheduledTaskAction -Execute "pythonw.exe" `
             -Argument "-m jarvis_desktop run"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$set     = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "Jarvis desktop agent" `
  -Action $action -Trigger $trigger -Settings $set -RunLevel Limited
```

`-RunLevel Limited`, not `Highest`. Put the token in a `config.json` under
`%APPDATA%\jarvis-desktop\` with ACLs restricted to your account.

---

## Teaching Jarvis your voice

So it can tell you from anybody else who speaks to it. The browser console can
do this too; this is the same thing from a machine with no browser open.

```sh
python -m jarvis_desktop enrol                  # record and send
python -m jarvis_desktop enrol --status         # whose voice is on file
python -m jarvis_desktop enrol --list-recorders # what this machine can use
python -m jarvis_desktop enrol --from-file me.wav
```

**No new dependency.** The only hard dependency of this agent is its websocket
client, and a microphone library — `sounddevice`, `pyaudio` — wants native code
for something you do three times in the life of an install. So it uses whatever
recorder the machine already has (`arecord`, `sox`'s `rec`, `afrecord`,
`ffmpeg`) and, if it has none, takes a WAV you recorded any way you like.

A supplied WAV is parsed rather than sent as-is: jarvis-core wants raw
little-endian 16-bit mono PCM, so a whole file would put the letters `RIFF` into
your voice profile — and nothing would error, because a header is 44 perfectly
valid bytes of int16. Stereo is mixed down rather than refused, and the rate
that travels with the audio is the rate the file **really** is, not the one that
was asked for: a recorder given `-r 16000` on a device that cannot do 16 kHz
hands back 48 kHz, and a profile built at the wrong declared rate matches
nobody.

The HTTP address is derived from `server_url` rather than configured again, so
you cannot enrol into a different Jarvis from the one this agent is paired with.

## Reading the audit log

```bash
python3 -m jarvis_desktop audit --limit 50
python3 -m jarvis_desktop audit --json | jq '.[] | select(.status == "denied")'
tail -f ~/.local/state/jarvis-desktop/audit.jsonl
```

One JSON object per line. Every dispatch writes exactly one — allowed,
asked-and-approved, asked-and-denied, denied outright, unsupported or crashed.
If it is not in there, it did not run.

```json
{"ts": 1786239708.097, "time": "2026-08-09T01:41:48", "action": "run_command",
 "params": {"command": "echo pwned"}, "tier": "CONFIRM", "decision": "DENY",
 "status": "denied", "ok": false, "source": "server", "duration_ms": 2,
 "error": "denied by the user", "command_id": "c-2",
 "note": "run_command local=CONFIRM requested=AUTO effective=CONFIRM, policy=ASK, -> ASK, approval=denied"}
```

The `note` field is the policy trace: local tier, what the server asked for,
what was enforced, your standing answer, and how it ended.

Parameters are redacted on the way to disk — anything under a key that looks
like a credential becomes `[redacted]`, long values are truncated. The **consent
prompt is not redacted**, deliberately: it is telling you what is about to
happen, and a masked value there would be a lie about what will run.

The log rotates at 8 MiB (keeping three archives) and compacts to the newest
5000 entries. An audit write can never fail a dispatch — every I/O error is
swallowed and logged.

---

## Layout

| file | what |
|---|---|
| `policy.py` | tiers, decisions, the JSON policy store. **Start here.** Pure logic. |
| `audit.py` | JSONL log, redaction, rotation |
| `consent.py` | the Tier-3 prompt: tkinter → terminal → deny, out loud |
| `theme.py` | the `--jv-*` palette, mirrored from `jarvis-web/src/lib/tokens.ts` |
| `status.py` | the status file a running agent maintains, and what `status` prints |
| `channel.py` | the WebSocket device protocol, reconnect, host pinning |
| `ratelimit.py` | token bucket, backoff, admission control. Pure logic, no clock. |
| `triggers.py` | cron arithmetic, file watch, idle, manual |
| `config.py` | config file, env, flags |
| `actions/registry.py` | the dispatcher — the single door every command comes through |
| `actions/paths.py` | the path-escape guard |
| `actions/ssrf.py` | the SSRF guard |
| `actions/shell.py` | `run_command`, the denylist, the environment scrub |
| `actions/builtins.py` | **the local tier table** |

Anything described as "pure logic" has no I/O, no clock and no OS calls, so it
is tested directly.

### The wire protocol

```
->  {"type":"auth","access_token":"..."}                     (only after auth_required)
<-  {"type":"auth_ok"}
->  {"id":1,"type":"jarvis/device/register",
     "device":{"id","name","platform":"desktop","capabilities":[…],
               "app_version","actions":[…]}}
<-  {"id":1,"type":"result","success":true,"result":{"ok":true}}

<-  {"type":"device_command","command_id":"c-1","action":"…","params":{…},
     "tier":1|2|3,"reason":"…"}
->  {"type":"device_result","command_id":"c-1",
     "status":"ok"|"denied"|"error"|"unsupported","result":{…},"error":"…"}

->  {"type":"device_event","event":"…","data":{…}}
```

Parsing rule for everything inbound: read the fields we know, ignore the rest,
never let an unknown field change behaviour. A server that adds
`"skip_confirmation": true` is describing a field this parser does not have and
will not grow.

The channel also enforces: exactly-once execution per `command_id` (a redelivery
replays the stored reply rather than running the action again), one in-flight
command per action id, a global concurrency cap, an inbound rate limit of ten
commands burst / one per second sustained, and host pinning.

Host pinning is checked twice, because once is not enough. The configured URL is
matched against `pinned_host` *before* connecting — and then, once the socket is
up, the `Host` of the handshake that actually happened is compared against the
host we dialled. The WebSocket client follows HTTP 3xx during the handshake,
cross-origin redirects included, so "the connection succeeded" is not the same
statement as "we are talking to the server we aimed at". The second check runs
before the token is sent: a redirected session is closed with the credential
still in this process.

---

## Tests

There are two suites and they answer different questions. `tests/` asks "is
each piece right?" against a fake socket. `tests_e2e/` asks "does the thing
work?" against a real server on a real socket. A change that breaks the
contract between them passes the first and fails the second, which is the
whole reason the second exists.

### Unit — `tests/`

```bash
cd jarvis-desktop
python3 -m pytest tests -q
```

722 tests, no network, no display, no hardware. The ones that carry weight:

* `test_policy.py` — the truth table, every combination of tier × requested tier
  × policy × switches × trust. Copied case for case from
  `android-app/tools/policy_truth_table_test.py`, so the phone and the desktop
  are checked against the *same* hand-written spec.
* `test_paths.py` — traversal, absolute paths, `~`, and the ones that matter:
  symlinks out of the workspace, symlinked parents, dangling symlinks, symlinked
  roots.
* `test_shell.py` — the denylist catches the catastrophes *and* leaves
  `git status` alone; the token never reaches a child process.
* `test_ssrf.py` — every legacy IPv4 spelling, IPv6 and v4-mapped forms, the DNS
  re-check, and that the exemption is exact rather than a suffix match.
* `test_channel.py` — the protocol against a fake socket: registration,
  command → result, tier-raise-only, rate limiting, dedupe, and that a denied
  command never reaches the action.
* `test_integration.py` — the injection story end to end: a poisoned file is
  read, the server asks for a shell command claiming Tier 1, and nothing runs.
  Also the escalation paths an adversarial review found: `launch_app` used as a
  shell, a notification title used as PowerShell, an `Authorization` header
  followed across a redirect.

### End to end — `tests_e2e/`

```bash
cd jarvis-desktop

# once: the harness boots the real jarvis-core, so its dependencies are needed
pip install -r ../jarvis-core/requirements.txt -r ../testing/requirements.txt

python3 -m pytest tests_e2e -q          # ~12s, 32 tests
python3 -m pytest tests_e2e -v          # names, if you want to watch it work
```

This starts a real `python -m jarvis` (via [`testing/harness`](../testing/),
which fakes only the model and voice backends, at the wire protocol) and a real
`python -m jarvis_desktop run` as a separate process, and drives the pair
through the actual `device_control` and `companion` services. If the shared
harness is not installed the whole suite skips with a sentence saying so,
rather than failing.

**Everything in it is the shipping code except two things.** CI has no human
and no screen, so the Tier-2/Tier-3 confirmation dialog and the
`companion.ask` question dialog are replaced by backends that read their answer
from a JSON file and record every prompt they were shown
(`tests_e2e/agent_runner.py`). That recording is the point: it turns "it asked
the user again" into an assertion. Both stubs fail closed — a missing or
unreadable control file is a denial — so a test that forgets to grant approval
cannot accidentally receive one.

What it proves that `tests/` cannot:

| invariant | what only a real run can show |
|---|---|
| **registration** | the real handshake — `auth_required` → `auth` → `auth_ok` → `jarvis/device/register` — lands a manifest on the server whose tiers are *this* machine's numbers, and the server's presence registry holds the device |
| **presence** | `device_event`/`presence` frames are actually emitted, applied to `DevicePresence`, and are what makes routing pick this machine when it is the only one connected |
| **Tier 1** | `get_system_state` returns real measurements of the machine it ran on, with nobody prompted |
| **Tier 3, refused** | the file is still on disk afterwards. `denied` proves the agent *said* no; the file proves `DeleteFile.run` was never called |
| **Tier 3, approved** | it runs exactly once and the next identical command prompts again — including when the prompt answers *always*, which Tier 3 never offers and the store refuses to keep. Checked against the prompt log and against a policy file that stays absent |
| **tier raising** | a `delete_file` tagged `tier: 1` still prompts at CONFIRM. And the case only the device can catch: `http_request` is NOTIFY in the manifest, so a POST arrives tagged **2** and is enforced at **3**, because `tier_for()` lives here and the server cannot see it |
| **the policy store** | a Tier-2 *always* really is written to `state/policy.json` and really does stop the next prompt — which is what makes "Tier 3 never wrote one" a statement about Tier 3 rather than about a file nobody ever writes. Then `never` and `panic`, edited into that file from outside, are picked up by the **running** agent: a kill switch that needs a restart is not a kill switch |
| **`companion.ask`** | the full cross-device round trip — service → `jarvis_message` → this desk → `jarvis_message_result` → the waiting service call resolves with the answer |
| **path escape / SSRF** | refused against a real filesystem (including a symlink out of the workspace) and a real resolver — *with approval already granted*, so it is the guard doing the refusing and not the policy engine |
| **reconnect** | the socket is cut mid-session by a TCP relay the test owns; the agent backs off, reconnects, re-registers on a new connection and re-reports presence, and the server's device list shows the gap in between |

Two habits keep those from passing for the wrong reason, and both are worth
copying if you add a test here:

* **Match the guard's own words, not a word the failure also contains.** Every
  refusal is asserted against the specific reason and the specific status the
  guard produces. `assert "refused" in error` looks fine until you notice that
  `[Errno 111] Connection refused` contains it — at which point a *deleted*
  SSRF guard passes on any machine where the target simply declines the
  connection.
* **Prove the reader works before trusting an empty one.** "Nothing was
  remembered" is read out of a file that does not exist on a green run, so on
  its own it is indistinguishable from reading the wrong path. One test makes
  the store exist on purpose; the rest lean on it.

`tests_e2e/test_support.py` covers the suite's own plumbing — the prompt-log
reader against a half-written line, `reset()` against the leftovers of an
earlier run, the policy file round-tripped through the shipping `PolicyStore`,
the TCP relay's cut and block, and the waits' failure messages. A bug in any of
those is invisible from inside the end-to-end tests, because a broken reader
and "nothing happened" look identical. Those tests need no server, but they
skip with the rest of the suite: CI treats an all-skipped run as a failure,
which is how a missing harness gets caught, and a handful of always-green tests
would quietly defeat it.

Artifacts: point `JARVIS_DESKTOP_E2E_WORK_DIR` (or `JARVIS_HARNESS_WORK_DIR`,
which CI sets) at a directory and everything is kept there —
`agent/agent.log`, the agent's `state/audit.jsonl` (and `state/policy.json`,
which only the policy-store test creates and removes again), every prompt it
showed in `control/prompts.jsonl`, every question in `control/asks.jsonl`, and
the harness's own `logs/jarvis-core.log`. Nothing in there carries the token.
The directory is not assumed to be empty: the agent clears its own state before
it starts, so re-running into the same one does not leave the closing sweep
reading the previous run's audit log. Both process logs are also attached to
any failure report, so a job that loses its artifacts is still diagnosable from
the console.

No test in the suite sleeps. Every wait is a poll for a condition with a
deadline, or a wait on an event the server fired, and every one of them names
what it was waiting for when it gives up.

What this suite still does **not** prove: that a real human sees a real dialog
(both are stubbed, by necessity); that the tiers behave the same on Windows or
macOS (it runs on the CI runner's Linux, and `screen_on` is reported from a
`DISPLAY` the fixture sets rather than from a real session); that the agent
survives a *server* restart as opposed to a socket cut; or anything about
`run_command`, the clipboard, input automation or screenshots, none of which
are available on a headless runner.
