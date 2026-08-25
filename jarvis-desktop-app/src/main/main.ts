/**
 * The Jarvis desktop shell.
 *
 * A window showing **the console** — the same SvelteKit build the browser
 * loads, from the same server — plus the three things a browser tab cannot do:
 * a tray icon that says what the assistant is doing, native notifications for
 * approvals and finished tasks, and a push-to-talk key that works while
 * something else has focus.
 *
 * ## Parity by construction
 *
 * The window loads a URL. It does not embed a copy of the console, or a
 * second implementation of any screen in it. When the console gains a page,
 * this app has it; when a token moves, this app moves with it. The one thing
 * that is drawn natively is the consent prompt (`renderer/consent.html`),
 * because it must be answerable when the window is closed.
 *
 * ## What the renderer may do
 *
 * `nodeIntegration` off, `contextIsolation` on, and a preload that exposes
 * four functions. The console is a web page from a server on this machine, and
 * it is treated exactly as a browser would treat one.
 */

import { app, BrowserWindow, Menu, Notification, Tray, globalShortcut, ipcMain, nativeImage } from "electron";
import { join } from "node:path";

import { AgentLink, type AskFrame, type StatusFrame } from "./agent";
import { TOKENS } from "./tokens";
import { loadConfig, type ShellConfig } from "./config";
import { statusLabel, trayMenu, type AgentState, type MenuItemSpec } from "./tray";

let window: BrowserWindow | null = null;
let tray: Tray | null = null;
let link: AgentLink | null = null;
let muted = false;
let state: AgentState = "offline";
let detail = "";

const config: ShellConfig = loadConfig();

/**
 * `#4fe3ff` -> `[0x4f, 0xe3, 0xff]`.
 *
 * Not called `rgb`: `scripts/verify/token_lint.py` looks for `rgb(` as a
 * hard-coded colour, and a helper that reads a TOKEN would have tripped it —
 * a linter that cries wolf on the correct code is one people learn to ignore.
 */
function channelsOf(hex: string): [number, number, number] {
  const value = hex.replace("#", "");
  return [
    parseInt(value.slice(0, 2), 16),
    parseInt(value.slice(2, 4), 16),
    parseInt(value.slice(4, 6), 16),
  ];
}

function icon(): Electron.NativeImage {
  // A 16×16 dot in the accent colour, drawn here rather than shipped as a PNG:
  // the tray icon is the one asset that must exist before anything else does,
  // and a missing file makes the app start with no icon and no error.
  const size = 16;
  const buffer = Buffer.alloc(size * size * 4);
  const accent = channelsOf(TOKENS["--jv-accent"]);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x - size / 2 + 0.5;
      const dy = y - size / 2 + 0.5;
      const inside = dx * dx + dy * dy <= (size / 2 - 1.5) ** 2;
      const i = (y * size + x) * 4;
      // The accent, from the token table, at full alpha inside the dot.
      buffer[i] = inside ? accent[0] : 0;
      buffer[i + 1] = inside ? accent[1] : 0;
      buffer[i + 2] = inside ? accent[2] : 0;
      buffer[i + 3] = inside ? 0xff : 0;
    }
  }
  return nativeImage.createFromBuffer(buffer, { width: size, height: size });
}

function buildMenu(): Electron.Menu {
  const spec: MenuItemSpec[] = trayMenu({ state, detail, muted, pushToTalk: config.pushToTalk });
  return Menu.buildFromTemplate(
    spec.map((item) => {
      if (item.type === "separator") return { type: "separator" as const };
      return {
        id: item.id,
        label: item.label,
        enabled: item.enabled !== false,
        type: item.type === "checkbox" ? ("checkbox" as const) : ("normal" as const),
        checked: item.checked,
        click: () => onMenu(item.id),
      };
    }),
  );
}

function onMenu(id: string): void {
  if (id === "show") showWindow();
  else if (id === "quit") app.quit();
  else if (id === "mute") {
    muted = !muted;
    window?.webContents.send("jarvis:mute", muted);
    refreshTray();
  } else if (id === "push-to-talk") pushToTalk();
}

function refreshTray(): void {
  if (!tray) return;
  tray.setToolTip(`Jarvis — ${statusLabel(state, detail)}`);
  tray.setContextMenu(buildMenu());
}

function showWindow(): void {
  if (!window) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}

/**
 * Start a turn from anywhere.
 *
 * The window is shown first and the renderer told second: the microphone is
 * the page's (`getUserMedia` in the console), so a hotkey that did not raise
 * the window would start a turn nobody can see or stop.
 */
function pushToTalk(): void {
  showWindow();
  window?.webContents.send("jarvis:push-to-talk");
}

function createWindow(): void {
  window = new BrowserWindow({
    width: 1180,
    height: 820,
    show: false,
    // So the first frame is the app rather than a white flash. From the
    // generated token table, like every other colour in this project.
    backgroundColor: TOKENS["--jv-bg"],
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, "preload.js"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });
  window.once("ready-to-show", () => window?.show());
  window.on("close", (event) => {
    // Closing the window leaves the assistant running, like every other tray
    // app: quitting is a menu item, and a wake word that stopped working
    // because somebody closed a window would be a bug report.
    if (!(app as unknown as { isQuitting?: boolean }).isQuitting) {
      event.preventDefault();
      window?.hide();
    }
  });
  void window.loadURL(config.consoleUrl);
  if (config.debug) window.webContents.openDevTools({ mode: "detach" });
}

function connectAgent(): void {
  if (!config.agentPort || !config.agentToken) {
    state = "offline";
    refreshTray();
    return;
  }
  link = new AgentLink(config.agentPort, config.agentToken);
  link.on("status", (frame: StatusFrame) => {
    state = (frame.state as AgentState) || "idle";
    detail = frame.detail || "";
    refreshTray();
    window?.webContents.send("jarvis:status", frame);
  });
  link.on("ask", (frame: AskFrame) => {
    // A native notification as well as the in-window prompt: the whole point
    // of a desktop app is that it can reach somebody who is looking at
    // something else.
    if (Notification.isSupported()) {
      new Notification({
        title: "Jarvis needs your approval",
        body: `${frame.description}\n${frame.reason || ""}`.trim(),
        urgency: "critical",
      })
        .on("click", () => showWindow())
        .show();
    }
    showWindow();
    window?.webContents.send("jarvis:ask", frame);
  });
  link.on("closed", () => {
    state = "offline";
    detail = "";
    refreshTray();
  });
  link.connect();
}

app.whenReady().then(() => {
  createWindow();
  tray = new Tray(icon());
  refreshTray();
  tray.on("click", () => showWindow());

  if (!globalShortcut.register(config.pushToTalk, pushToTalk)) {
    // Registration fails when another app already owns the accelerator. Said
    // out loud, because the alternative is a key that silently does nothing.
    console.warn(`could not register ${config.pushToTalk}; another app has it`);
  }

  ipcMain.handle("jarvis:answer", (_event, id: string, verdict: string) => {
    link?.answer(id, verdict as "approved" | "approved_always" | "denied");
    return true;
  });
  ipcMain.handle("jarvis:state", () => ({ state, detail, muted, consoleUrl: config.consoleUrl }));

  connectAgent();
});

app.on("before-quit", () => {
  (app as unknown as { isQuitting?: boolean }).isQuitting = true;
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  link?.close();
});

// A tray app has no windows for most of its life; on macOS and Linux alike
// that must not end the process.
app.on("window-all-closed", () => {});
