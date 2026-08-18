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

test("voice is available in chat mode, and only on request", async ({ page }) => {
  // Two halves of the same requirement. Switching to typing is not "stop
  // listening to me" — the button is right there. But chat mode must not
  // transcribe the room: an always-on VAD at a keyboard turns every remark
  // made nearby into a turn, and each one into a row in the history sidebar.
  await page.goto("/?mode=chat");
  const mic = page.getByTestId("chat-mic");
  await expect(mic).toBeVisible({ timeout: 10_000 });
  await expect(mic).toHaveAttribute("aria-pressed", "false");
  await expect(mic).toContainText("SPEAK");

  // Nothing has been asked, so nothing is in the transcript and nothing has
  // been filed. Headless Chromium has no microphone, which is exactly the
  // ambient case: the page must sit still rather than start a turn.
  await expect(page.getByTestId("chat-empty")).toBeVisible();
  await page.waitForTimeout(1500);
  await expect(page.getByTestId("chat-message")).toHaveCount(0);
});

test("the mode toggle does not sit on top of the corner controls", async ({
  page,
}) => {
  // It used to be one `position: fixed` button in the top-right, which is
  // where the HUD keeps its status readout and clock and where chat mode keeps
  // the speak toggle — so it covered both, and being fixed it ate their clicks.
  await page.goto("/");
  const toggle = page.getByTestId("mode-toggle");
  const status = page.getByTestId("status");
  await expect(toggle).toBeVisible({ timeout: 10_000 });

  const overlaps = async () => {
    const a = await toggle.boundingBox();
    const b = await status.boundingBox();
    if (!a || !b) throw new Error("a control has no box");
    return (
      a.x < b.x + b.width && b.x < a.x + a.width &&
      a.y < b.y + b.height && b.y < a.y + a.height
    );
  };
  expect(await overlaps(), "the switch covers the HUD status readout").toBe(false);

  // ...and the same in chat mode, against the control it shares a row with.
  await toggle.click();
  const speak = page.getByTestId("chat-speak");
  await expect(speak).toBeVisible();
  const t = await toggle.boundingBox();
  const sp = await speak.boundingBox();
  if (!t || !sp) throw new Error("a control has no box");
  expect(
    t.x < sp.x + sp.width && sp.x < t.x + t.width &&
      t.y < sp.y + sp.height && sp.y < t.y + t.height,
    "the switch covers the speak toggle",
  ).toBe(false);
  // Still clickable, which is what the overlap actually broke.
  await speak.click();
  await expect(speak).toHaveAttribute("aria-pressed", "true");
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
