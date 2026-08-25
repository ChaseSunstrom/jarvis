import { test, expect } from '@playwright/test';

/**
 * What Jarvis remembers, and the two buttons the model does not get.
 *
 * The memory integration's promise is that this is the user's data: local,
 * readable, deletable, portable. Everything below is that promise as a page —
 * a person can see every note, tell what they said from what Jarvis worked
 * out, delete one, leave with the lot, or delete all of it.
 */

test('every note is listed, with where it came from', async ({ page }) => {
	await page.goto('/memory');
	await expect(page.getByTestId('memory-lede')).toBeVisible({ timeout: 15_000 });

	const list = page.getByTestId('memory-list');
	await expect(list).toContainText('spare key');
	await expect(list).toContainText('drink tea');

	// The distinction that matters: told, versus worked out. A user who cannot
	// tell those apart cannot audit what an assistant has decided about them.
	await expect(page.getByTestId('memory-source-mem1')).toHaveText('user');
	await expect(page.getByTestId('memory-source-mem2')).toHaveText('extracted');
	await expect(page.getByTestId('memory-lede')).toContainText('worked out');
});

test('a note can be forgotten, and it goes', async ({ page }) => {
	await page.goto('/memory');
	await expect(page.getByTestId('memory-entry-mem2')).toBeVisible({ timeout: 15_000 });

	await page.getByTestId('memory-forget-mem2').click();
	await expect(page.getByTestId('memory-entry-mem2')).toHaveCount(0);
	await expect(page.getByTestId('memory-list')).toContainText('spare key');
});

test('export is a file, not a screen', async ({ page }) => {
	// "You can leave with your data" means a download. The browser cannot hold
	// the backend token, so it goes through the console's own route.
	await page.goto('/memory');
	await expect(page.getByTestId('memory-lede')).toBeVisible({ timeout: 15_000 });

	const answer = await page.request.get('/api/memory/export?format=json');
	expect(answer.ok()).toBe(true);
	expect(answer.headers()['content-disposition']).toContain('jarvis-memory.json');
	const payload = await answer.json();
	expect(payload.count).toBeGreaterThan(0);

	const markdown = await page.request.get('/api/memory/export?format=markdown');
	expect(markdown.ok()).toBe(true);
	expect(await markdown.text()).toContain('# What Jarvis remembers');
});

test('forgetting everything asks first, and then means it', async ({ page }) => {
	await page.goto('/memory');
	await expect(page.getByTestId('memory-list')).toBeVisible({ timeout: 15_000 });

	// One click arms it; the destructive button is the second one. This is the
	// only irreversible control in the console, and it is the user's — the
	// model has no tool that can reach it.
	await page.getByTestId('memory-wipe').click();
	await expect(page.getByTestId('memory-wipe-confirm')).toBeVisible();
	await page.getByTestId('memory-wipe-cancel').click();
	await expect(page.getByTestId('memory-list')).toBeVisible();

	await page.getByTestId('memory-wipe').click();
	await page.getByTestId('memory-wipe-confirm').click();
	await expect(page.getByTestId('memory-empty')).toBeVisible({ timeout: 10_000 });
});
