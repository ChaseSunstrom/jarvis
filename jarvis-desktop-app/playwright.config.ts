import { defineConfig } from "@playwright/test";

/**
 * The shell, driven for real.
 *
 * `_electron.launch` starts the actual app — main process, preload, tray,
 * global shortcut — under Xvfb. There is no device and no display; what is
 * proved is that the thing starts, loads the console, registers its hotkey and
 * exposes exactly the API the console feature-detects, which is the set of
 * things a unit test with Electron mocked cannot say anything about.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { trace: "off" },
});
