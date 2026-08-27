# jarvis-desktop-app — the console in a native shell

An Electron window that loads **the console** — the same SvelteKit build a
browser loads, from the same server — plus the three things a browser tab
cannot do:

* a **tray icon** that says what the assistant is doing, with mute,
  push-to-talk and quit;
* **native notifications** when Jarvis needs an approval or a job finishes;
* a **push-to-talk key** (`Super+Space`) that starts a turn while something
  else has focus.

## Parity by construction

The window loads a URL. There is no copy of any console screen in this
repository, so when the console gains a page this app has it, and when a design
token moves this app moves with it. The single exception is
`src/renderer/consent.html`, and it earns the exception: an approval must be
answerable when the console is not loaded — the server is down, the window has
never been opened, the machine has just woken — and the thing that asks
permission is the last surface that may depend on a network. It draws from
`src/renderer/tokens.css`, which `design/build.py` generates from the same
`design/tokens.json` as everything else.

## Running it

```bash
cd jarvis-desktop-app
npm install
npm start                       # builds, then launches
```

| Variable | What it does |
|---|---|
| `JARVIS_CONSOLE_URL` | which console to load. Default `http://127.0.0.1:8199`. Loopback, the tailnet (`*.ts.net`, `100.64/10`) and private LAN ranges only — anything else is refused and the default is used, because the window is a browser pointed at whatever this says |
| `JARVIS_AGENT_PORT` / `JARVIS_AGENT_TOKEN` | where the desktop agent's IPC socket is. Read from the agent's own state directory when unset |
| `JARVIS_PUSH_TO_TALK` | the accelerator. Default `Super+Space`, chosen because Ctrl+Shift+letter collides with whatever you are typing into |
| `JARVIS_DESKTOP_DEBUG=1` | open devtools |

## The agent, and this

Two programs, one product:

* **`jarvis-desktop/`** (Python) is the agent. It runs actions on this machine,
  keeps the policy, and asks before anything at Tier 2 or 3.
* **`jarvis-desktop-app/`** (this) is the shell. It shows the console, and it
  is where those questions are answered.

They meet over a loopback socket with a token
(`jarvis_desktop/ipc.py`). When no shell is connected the agent falls back to
its Tk dialog and then to a terminal prompt, and a question nobody can be asked
is refused rather than assumed — the same rule everywhere in this project.

## Testing it here

```bash
npm test                                   # vitest: config, tray, frame parsing
bash tools/xvfb.sh npx playwright test     # the real app, under Xvfb
npm run dist:dir                           # an unpacked build
```

Two host notes, both the same constraint as the rest of the repository —
nothing is installed system-wide:

* `tools/electron-runtime.sh` unpacks Electron's GTK/NSS/ALSA closure under
  `~/.local/electron-runtime` with `apt-get download` + `dpkg -x`, needing no
  root.
* `tools/xvfb.sh` starts `Xvfb` itself rather than using `xvfb-run`, which
  needs `xauth`.

What the unit tests cover is what the shell DECIDES — which URL it will load,
what the tray says, how frames are parsed — because Electron cannot be imported
into a plain Node test and mocking it would only assert the mock. What the
Playwright run covers is that the real thing starts, loads, exposes exactly
seven functions to the page and no `require`, and holds its global shortcut.

## Starting from a downloaded folder (Linux)

`jarvis-desktop-app-linux` is a plain folder, and a folder cannot carry a
setuid-root `chrome-sandbox` — an archive keeps no owner, and unpacking as
yourself makes you the owner. On a distribution that also restricts
unprivileged user namespaces (Ubuntu 24.04's AppArmor default) Chromium
would abort with "The SUID sandbox helper binary was found, but is not
configured correctly". The app checks the helper itself before it starts
(root, setuid, executable — Chromium's own three) and, when it is not
usable, turns Chromium's *process* sandbox off for the run and says so on
stderr. The window's renderer stays sandboxed and context-isolated; the
console it shows is your own server. No `chmod`, no `sudo` (running as root
is refused by Electron anyway, and `--no-sandbox` under `sudo` loses the
display).

If nothing answers at the console URL yet, the app shows **No console
there yet** with the URL it tried and how to point it elsewhere
(`JARVIS_CONSOLE_URL=http://<host>:8199`), and keeps trying every five
seconds — bringing the stack up is enough. A renderer that dies is said on
stderr with Chromium's reason rather than left as a blank window.
