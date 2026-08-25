import { describe, expect, it } from "vitest";

import {
  DEFAULT_CONSOLE,
  DEFAULT_PUSH_TO_TALK,
  isAllowedConsole,
  loadConfig,
  readAgentToken,
} from "../src/main/config";
import { statusLabel, trayMenu } from "../src/main/tray";
import { parseFrames } from "../src/main/agent";

// Electron is not importable in a plain Node test, which is why every decision
// worth testing lives in a module that does not import it. What is asserted
// here is what the shell will LOAD and what it will TRUST — the two questions
// a shell around somebody's assistant has to get right.

describe("which console the shell will load", () => {
  it("accepts loopback", () => {
    expect(isAllowedConsole("http://127.0.0.1:8199")).toBe(true);
    expect(isAllowedConsole("http://localhost:8199")).toBe(true);
  });

  it("accepts the tailnet and the LAN", () => {
    expect(isAllowedConsole("https://jarvis.tail05d9af.ts.net")).toBe(true);
    expect(isAllowedConsole("http://100.101.102.103:8199")).toBe(true);
    expect(isAllowedConsole("http://192.168.1.20:8199")).toBe(true);
  });

  it("refuses the internet", () => {
    // The window has node integration off and a four-function preload — and it
    // is still a browser pointed at whatever this string says, and the string
    // comes from the environment.
    expect(isAllowedConsole("https://example.com")).toBe(false);
    expect(isAllowedConsole("http://8.8.8.8")).toBe(false);
  });

  it("refuses anything that is not http", () => {
    expect(isAllowedConsole("file:///etc/passwd")).toBe(false);
    expect(isAllowedConsole("javascript:alert(1)")).toBe(false);
    expect(isAllowedConsole("not a url at all")).toBe(false);
  });

  it("falls back to loopback rather than loading a refused url", () => {
    const config = loadConfig({ JARVIS_CONSOLE_URL: "https://example.com" } as NodeJS.ProcessEnv);
    expect(config.consoleUrl).toBe(DEFAULT_CONSOLE);
  });

  it("uses the one it was given when it is allowed", () => {
    const config = loadConfig({
      JARVIS_CONSOLE_URL: "http://100.64.0.9:8199",
    } as NodeJS.ProcessEnv);
    expect(config.consoleUrl).toBe("http://100.64.0.9:8199");
  });
});

describe("the rest of the configuration", () => {
  it("has a push-to-talk key that is not a text-editing shortcut", () => {
    expect(DEFAULT_PUSH_TO_TALK).toContain("Super");
    expect(loadConfig({} as NodeJS.ProcessEnv).pushToTalk).toBe(DEFAULT_PUSH_TO_TALK);
  });

  it("ignores an impossible agent port", () => {
    expect(loadConfig({ JARVIS_AGENT_PORT: "99999" } as NodeJS.ProcessEnv).agentPort).toBe(0);
    expect(loadConfig({ JARVIS_AGENT_PORT: "nonsense" } as NodeJS.ProcessEnv).agentPort).toBe(0);
  });

  it("reads no token when the agent has never run", () => {
    expect(readAgentToken("/tmp/nowhere-at-all-" + Math.random())).toBe("");
  });
});

describe("what the tray says", () => {
  it("names the state in words a person recognises", () => {
    expect(statusLabel("listening")).toBe("Listening");
    expect(statusLabel("thinking", "reading a page")).toBe("Working — reading a page");
  });

  it("does not call a missing agent an error", () => {
    // The ordinary case for anybody who has not started it; an error icon on a
    // first run is a support ticket.
    expect(statusLabel("offline")).toBe("Agent not running");
  });

  it("offers the four things a tray is for", () => {
    const ids = trayMenu({ state: "idle", muted: false, pushToTalk: "Super+Space" }).map((i) => i.id);
    expect(ids).toContain("show");
    expect(ids).toContain("push-to-talk");
    expect(ids).toContain("mute");
    expect(ids).toContain("quit");
  });

  it("says what the mute item will DO, not what it is", () => {
    const muted = trayMenu({ state: "idle", muted: true, pushToTalk: "Super+Space" });
    expect(muted.find((i) => i.id === "mute")?.label).toBe("Unmute the microphone");
  });

  it("puts the accelerator in the label, not in the menu item", () => {
    // It is a GLOBAL shortcut; an `accelerator` field here would register it
    // twice and the second registration fails silently.
    const item = trayMenu({ state: "idle", muted: false, pushToTalk: "Super+Space" })
      .find((i) => i.id === "push-to-talk");
    expect(item?.label).toContain("Super+Space");
  });
});

describe("the agent's frames", () => {
  it("reads whole lines and ignores half-written ones", () => {
    const frames = parseFrames('{"type":"status","state":"idle"}\n{"broken"\n{"type":"ask","id":"1"}\n');
    expect(frames.map((f) => f.type)).toEqual(["status", "ask"]);
  });
});
