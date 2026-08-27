import { _electron as electron, expect, test, type ElectronApplication } from "@playwright/test";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

/**
 * The first run, and the operator's 27 Aug 2026 run: nothing listens where
 * the console should be, and the app was a blank window with
 * ERR_CONNECTION_REFUSED on stderr. Now it is a page that names the URL and
 * says how to point the app elsewhere — and it keeps trying, so bringing
 * the stack up is enough.
 *
 * And the sandbox: this launch runs Electron from node_modules, whose
 * chrome-sandbox is owned by whoever ran npm — the same shape as a folder
 * somebody downloaded — so the main process must have turned the process
 * sandbox switch on by itself, before ready.
 */
let app: ElectronApplication;
let closedPort: number;

test.beforeAll(async () => {
  // A port nothing listens on: open a server to learn a free port, close it.
  const probe: Server = createServer();
  await new Promise<void>((resolve) => probe.listen(0, "127.0.0.1", () => resolve()));
  closedPort = (probe.address() as AddressInfo).port;
  await new Promise<void>((resolve) => probe.close(() => resolve()));
  app = await electron.launch({
    args: [".", ...(process.env.CI ? ["--no-sandbox"] : [])],
    env: {
      ...process.env,
      JARVIS_CONSOLE_URL: `http://127.0.0.1:${closedPort}`,
      JARVIS_AGENT_PORT: "",
      JARVIS_AGENT_TOKEN: "",
    },
  });
});

test.afterAll(async () => {
  await app?.close();
});

test("no console at the URL: a page that names it and how to point elsewhere, not a blank window", async () => {
  const window = await app.firstWindow();
  await expect(window.getByTestId("unreachable")).toBeVisible({ timeout: 15_000 });
  await expect(window.getByTestId("unreachable-url")).toContainText(`http://127.0.0.1:${closedPort}`);
  await expect(window.getByTestId("unreachable")).toContainText("JARVIS_CONSOLE_URL");
});

test("from a folder whose chrome-sandbox is not setuid root, the process sandbox switch is set before ready", async () => {
  test.skip(process.platform !== "linux", "the setuid helper is a Linux thing");
  const decided = await app.evaluate(({ app: electronApp }) => electronApp.commandLine.hasSwitch("no-sandbox"));
  expect(decided).toBe(true);
});
