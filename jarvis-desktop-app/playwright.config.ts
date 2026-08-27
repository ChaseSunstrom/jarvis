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
  // On CI the github reporter annotates each failed test on the check run
  // (readable on the public API without a token, which the job log is not)
  // and the html report is what the workflow uploads on failure; the shell
  // suite failed twice on CI with "exit code 1" and nothing else to go on.
  reporter: process.env.CI
    ? [["list"], ["github"], ["html", { open: "never", outputFolder: "playwright-report" }]]
    : [["list"]],
  use: { trace: "off" },
});
