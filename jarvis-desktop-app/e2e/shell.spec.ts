import { _electron as electron, expect, test, type ElectronApplication } from "@playwright/test";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

/**
 * The shell, started for real under Xvfb.
 *
 * A tiny HTTP server stands in for the console — the point of these tests is
 * the SHELL: that it starts, loads the URL it was given, exposes exactly the
 * API the console feature-detects, keeps running when its window is closed,
 * and registers a global shortcut. Whether the console renders is the
 * console's own suite's business, and it has one.
 */

let app: ElectronApplication;
let server: Server;
let consoleUrl: string;

test.beforeAll(async () => {
  server = createServer((_request, response) => {
    response.writeHead(200, { "content-type": "text/html" });
    response.end('<!doctype html><title>console</title><h1 data-testid="fake-console">CONSOLE</h1>');
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  consoleUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;

  app = await electron.launch({
    args: ["."],
    env: {
      ...process.env,
      JARVIS_CONSOLE_URL: consoleUrl,
      // No agent: the shell must be usable without one, which is the state
      // anybody is in before they start the desktop agent for the first time.
      JARVIS_AGENT_PORT: "",
      JARVIS_AGENT_TOKEN: "",
    },
  });
});

test.afterAll(async () => {
  await app?.close();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

test("the window loads the console it was pointed at", async () => {
  const window = await app.firstWindow();
  await expect(window.getByTestId("fake-console")).toBeVisible();
  expect(window.url()).toContain(consoleUrl);
});

test("the page is given the shell's API and nothing else", async () => {
  const window = await app.firstWindow();
  const api = await window.evaluate(() => {
    const shell = (window as unknown as { jarvisDesktop?: Record<string, unknown> }).jarvisDesktop;
    return {
      present: Boolean(shell?.present),
      keys: shell ? Object.keys(shell).sort() : [],
      // The two things a preload exists to keep OUT of a page.
      node: typeof (window as unknown as { require?: unknown }).require,
      process: typeof (window as unknown as { process?: unknown }).process,
    };
  });
  expect(api.present).toBe(true);
  expect(api.keys).toEqual([
    "answer",
    "onAsk",
    "onMute",
    "onPushToTalk",
    "onStatus",
    "present",
    "state",
  ]);
  expect(api.node).toBe("undefined");
  expect(api.process).toBe("undefined");
});

test("the push-to-talk key is registered globally", async () => {
  const registered = await app.evaluate(async ({ globalShortcut }) =>
    globalShortcut.isRegistered("Super+Space"),
  );
  expect(registered).toBe(true);
});

test("closing the window hides it rather than quitting", async () => {
  const window = await app.firstWindow();
  await app.evaluate(async ({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()[0].close();
  });
  const alive = await app.evaluate(async ({ BrowserWindow }) => BrowserWindow.getAllWindows().length);
  // A tray app that quit when its window closed would stop the wake word
  // because somebody tidied their desktop.
  expect(alive).toBe(1);
  await app.evaluate(async ({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()[0].show();
  });
  await expect(window.getByTestId("fake-console")).toBeVisible();
});
