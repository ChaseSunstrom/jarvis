import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // `tests/` only. `e2e/` is Playwright's — it launches a real Electron and
    // means nothing to vitest, which was collecting it, failing on
    // `@playwright/test`'s import, and reporting "1 failed | 15 passed" with
    // an exit code of 0. A suite that fails without failing is worse than one
    // that does not exist.
    include: ["tests/**/*.test.ts"],
    environment: "node",
  },
});
