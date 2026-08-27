/**
 * Everything the shell needs to know before it draws anything, and where each
 * of them comes from.
 *
 * Pure, and separately tested (`tests/config.test.ts`): the shell's decisions
 * about *what to load* and *what to trust* are exactly the ones worth having
 * unit tests for, and Electron cannot be imported into a plain Node test.
 */

import { readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export interface ShellConfig {
  /** The console to load. Loopback by default; the tailnet is a valid choice. */
  consoleUrl: string;
  /** Where the desktop agent's IPC socket is, if it is running. */
  agentPort: number;
  /** The token that socket demands. Empty when the agent has not started. */
  agentToken: string;
  /** The accelerator that starts a turn from anywhere. */
  pushToTalk: string;
  /** True to open devtools; only ever from the environment. */
  debug: boolean;
}

/** The console's own default port — the same one `docker-compose.yml` publishes. */
export const DEFAULT_CONSOLE = "http://127.0.0.1:8199";

/**
 * The hotkey.
 *
 * `Super` rather than a letter with Ctrl+Shift: those collide with whatever
 * the user is typing into, and a push-to-talk key that steals a shortcut from
 * the editor somebody is working in gets turned off on the first day.
 */
export const DEFAULT_PUSH_TO_TALK = "Super+Space";

/** Where the agent writes its token (`jarvis_desktop.ipc.default_directory`). */
export function agentStateDir(env: NodeJS.ProcessEnv = process.env): string {
  const base = env.XDG_STATE_HOME || env.LOCALAPPDATA || join(homedir(), ".local", "state");
  return join(base, "jarvis-desktop");
}

/**
 * Whether a URL is one the shell may load.
 *
 * The window has node integration off and a preload that exposes four
 * functions — but it is still a browser pointed at whatever this string says,
 * and the string comes from the environment. Loopback and the tailnet are
 * addresses a person chose; anything else is a mistake or an attack, and the
 * shell refuses rather than rendering it.
 */
export function isAllowedConsole(url: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
  const host = parsed.hostname;
  if (host === "127.0.0.1" || host === "localhost" || host === "::1") return true;
  // Tailscale: the `*.ts.net` MagicDNS name and the 100.64.0.0/10 CGNAT range
  // the tailnet lives in. Both are "a machine I own", which is the whole of
  // what this check is trying to express.
  if (host.endsWith(".ts.net")) return true;
  const parts = host.split(".").map((p) => Number(p));
  if (parts.length === 4 && parts.every((n) => Number.isInteger(n) && n >= 0 && n <= 255)) {
    if (parts[0] === 100 && parts[1] >= 64 && parts[1] <= 127) return true;
    if (parts[0] === 10) return true;
    if (parts[0] === 192 && parts[1] === 168) return true;
    if (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) return true;
  }
  return false;
}

/** Read the agent's token, or "" when the agent has not run. */
export function readAgentToken(dir: string = agentStateDir()): string {
  try {
    return readFileSync(join(dir, "shell-token"), "utf8").trim();
  } catch {
    return "";
  }
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): ShellConfig {
  const wanted = (env.JARVIS_CONSOLE_URL || DEFAULT_CONSOLE).trim();
  const consoleUrl = isAllowedConsole(wanted) ? wanted : DEFAULT_CONSOLE;
  const port = Number(env.JARVIS_AGENT_PORT || 0);
  return {
    consoleUrl,
    agentPort: Number.isInteger(port) && port > 0 && port < 65536 ? port : 0,
    agentToken: env.JARVIS_AGENT_TOKEN || readAgentToken(),
    pushToTalk: (env.JARVIS_PUSH_TO_TALK || DEFAULT_PUSH_TO_TALK).trim() || DEFAULT_PUSH_TO_TALK,
    debug: env.JARVIS_DESKTOP_DEBUG === "1",
  };
}

/**
 * Whether Chromium's setuid sandbox helper can be used from `path`.
 *
 * A folder somebody downloaded and unpacked cannot carry a setuid root file
 * (an archive does not keep the owner, and unpacking as a user makes the
 * user the owner), and on a distribution that also restricts unprivileged
 * user namespaces (Ubuntu 24.04's AppArmor default) Chromium has no third
 * sandbox to fall back to — it aborts with "The SUID sandbox helper binary
 * was found, but is not configured correctly". This is the check Chromium
 * makes, made first: owned by root, setuid, executable.
 *
 * What it does NOT decide: whether the namespace sandbox would have worked.
 * That is only knowable by starting, and a start that dies is the report
 * this exists to prevent (27 Aug 2026, `~/Downloads/jarvis-desktop-app-linux`).
 */
export function sandboxHelperUsable(
  path: string,
  stat: (p: string) => { uid: number; mode: number } = (p) => statSync(p),
): boolean {
  try {
    const info = stat(path);
    return info.uid === 0 && (info.mode & 0o4000) !== 0 && (info.mode & 0o111) !== 0;
  } catch {
    return false;
  }
}
