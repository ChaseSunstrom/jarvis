import { test, expect } from "@playwright/test";

/**
 * Chat mode, against the built app and the mock backend.
 *
 * These are the claims the feature is: it is a toggle rather than a second
 * app, a typed question runs the real pipeline, the working is shown inline
 * and in order, and past conversations are listed and can be reopened.
 *
 * `?mode=chat` opens straight into it — the mode is otherwise remembered in
 * localStorage, and a suite that clicked its way in would be testing the click
 * on every one of these.
 */

test("the toggle switches between the orb and chat, and is remembered", async ({
  page,
}) => {
  await page.goto("/");
  // The orb is the default: nothing about chat mode changes what a fresh
  // install shows.
  await expect(page.getByTestId("mic")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("chat-panel")).toHaveCount(0);

  await page.getByTestId("mode-toggle").click();
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("chat-input")).toBeVisible();

  // Remembered across a reload, because a mode that resets is a mode nobody
  // uses twice.
  await page.reload();
  await expect(page.getByTestId("chat-panel")).toBeVisible({ timeout: 10_000 });

  // ...and back.
  await page.getByTestId("mode-toggle").click();
  await expect(page.getByTestId("chat-panel")).toHaveCount(0);
  await expect(page.getByTestId("mic")).toBeVisible();
});

test("a typed question streams an answer, with its tools and reasoning inline", async ({
  page,
}) => {
  await page.goto("/?mode=chat");
  const input = page.getByTestId("chat-input");
  await expect(input).toBeVisible({ timeout: 10_000 });

  await input.fill("turn on the lab lights");
  await page.getByTestId("chat-send").click();

  // The question appears immediately — it does not wait for a transcript,
  // because there is no transcription coming.
  await expect(
    page.getByTestId("chat-message").filter({ hasText: "turn on the lab lights" }),
  ).toBeVisible({ timeout: 10_000 });

  // The tool row is drawn, and it is drawn as having succeeded.
  const toolRow = page.getByTestId("chat-tool-turn_on");
  await expect(toolRow).toBeVisible({ timeout: 15_000 });
  await expect(toolRow).toContainText("turn_on");
  await expect(toolRow).toContainText("lab lights");

  // The reasoning is present and COLLAPSED. Both halves matter: it has to be
  // readable, and it must not be presented as the answer.
  const thinking = page.getByTestId("chat-thinking");
  await expect(thinking).toBeVisible();
  await expect(thinking).not.toHaveAttribute("open", /.*/);
  await thinking.getByRole("group").or(thinking.locator("summary")).first().click();
  await expect(thinking).toContainText("the lab strip");

  // And the answer itself.
  await expect(
    page.getByTestId("chat-message").filter({ hasText: "Turning on the lab lights." }),
  ).toBeVisible({ timeout: 15_000 });

  // The turn settles rather than spinning forever.
  await expect(
    page.locator('[data-testid="chat-message"][data-role="assistant"][data-pending="true"]'),
  ).toHaveCount(0, { timeout: 15_000 });
});

test("past conversations are listed, reopened and forgotten", async ({ page }) => {
  await page.goto("/?mode=chat");
  await expect(page.getByTestId("chat-input")).toBeVisible({ timeout: 10_000 });

  // Seeded by the mock, so the sidebar is never empty on first paint.
  const earlier = page
    .getByTestId("chat-conversation")
    .filter({ hasText: "is the back door shut?" });
  await expect(earlier).toBeVisible({ timeout: 10_000 });

  await earlier.click();
  // Reopening restores the whole turn — the answer, and the tool call it made.
  await expect(
    page.getByTestId("chat-message").filter({ hasText: "It is, Sir." }),
  ).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("chat-tool-get_state")).toBeVisible();
  // A stored call has already happened; it must not render as still running.
  await expect(page.getByTestId("chat-tool-get_state")).not.toContainText("…");

  // A new conversation clears the view without touching the archive.
  await page.getByTestId("chat-new").click();
  await expect(page.getByTestId("chat-empty")).toBeVisible();
  await expect(earlier).toBeVisible();

  // Forgetting removes the row.
  await earlier.hover();
  await earlier.locator("xpath=..").getByTestId("chat-delete").click();
  await expect(earlier).toHaveCount(0, { timeout: 10_000 });
});

test("the microphone is still live in chat mode", async ({ page }) => {
  // The requirement in one test: switching to typing is not "stop listening".
  await page.goto("/?mode=chat");
  const mic = page.getByTestId("chat-mic");
  await expect(mic).toBeVisible({ timeout: 10_000 });
  await expect(mic).toHaveAttribute("aria-pressed", "false");
  await expect(mic).toContainText("MIC");

  // And the mute is a real kill switch here too.
  await mic.click();
  await expect(mic).toHaveAttribute("aria-pressed", "true");
  await expect(mic).toContainText("MUTED");
});

test("typed replies are silent by default and can be made to speak", async ({
  page,
}) => {
  await page.goto("/?mode=chat");
  const speak = page.getByTestId("chat-speak");
  await expect(speak).toBeVisible({ timeout: 10_000 });
  // Off by default: a console left open in a bedroom must not read its replies
  // to the room.
  await expect(speak).toHaveAttribute("aria-pressed", "false");
  await expect(speak).toContainText("SILENT");

  await speak.click();
  await expect(speak).toHaveAttribute("aria-pressed", "true");
  await expect(speak).toContainText("SPEAKS");
});
