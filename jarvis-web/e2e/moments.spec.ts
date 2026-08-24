import { test, expect } from '@playwright/test';

/**
 * The things Jarvis said while nobody was looking.
 *
 * `docs/AUDIT.md` §15: deliveries were "companion pushes and toasts, not
 * designed UI moments; no notification record to retrieve". A toast is gone in
 * four seconds and these arrive when you are not at the screen — so they
 * accumulate in an inbox, each one saying what happened, what it found, where
 * to go, and **why you are seeing it**.
 */

test('the inbox lists what arrived, with an unread count', async ({ page }) => {
	await page.goto('/tasks');
	await expect(page.getByTestId('notifications')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('notifications-unread')).toHaveText('1');

	await page.getByTestId('notifications-toggle').click();
	const list = page.getByTestId('notifications-list');
	await expect(list).toContainText('research on heat pumps');
	await expect(list).toContainText('Morning briefing');
});

test('a moment says why it is on the screen', async ({ page }) => {
	// The honest answer: the bus event that produced it. Not a sentence the
	// model wrote about itself afterwards.
	await page.goto('/tasks');
	await page.getByTestId('notifications-toggle').click();

	await page.getByTestId('moment-note1-why').click();
	await expect(page.getByTestId('moment-note1-source')).toContainText('jarvis_task_completed');
});

test('reading one is not dismissing it', async ({ page }) => {
	await page.goto('/tasks');
	await page.getByTestId('notifications-toggle').click();

	await page.getByTestId('moment-note1-read').click();
	await expect(page.getByTestId('notifications-unread')).toHaveCount(0);
	// Still there: reading something is not throwing it away.
	await expect(page.getByTestId('notifications-list')).toContainText('research on heat pumps');

	await page.getByTestId('moment-note1-dismiss').click();
	await expect(page.getByTestId('notifications-list')).not.toContainText('research on heat pumps');
});

test('a moment links to the thing itself', async ({ page }) => {
	// The briefing rather than the task: the test above dismisses that one, and
	// the tests in a file share one mock — a test that depends on its
	// neighbours is a test that fails for the wrong reason.
	await page.goto('/tasks');
	await page.getByTestId('notifications-toggle').click();
	await expect(page.getByTestId('moment-note2-open')).toHaveAttribute('href', '/');
});
