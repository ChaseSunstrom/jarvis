import { expect, test, type Page } from '@playwright/test';

/**
 * M111 — the operator's report of 27 Aug 2026: "the tasks taking up the
 * entire screen on the voice tab and causing a scroll; it should just be a
 * simple brief with the task, that I can then expand". Four running tasks
 * under the instrument: one line each, the page does not scroll, a click
 * opens one to its bar, sentence and steps, and a second click folds it.
 */
const tell = (page: Page, frame: Record<string, unknown>) =>
	page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
				ws.onopen = () => ws.send(JSON.stringify({ id: 91, ...payload }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		frame
	);

const TITLES = ['Read every light in the house', 'Audit the sensors', 'Write up the cheap-rate report', 'Watch the front door'];

async function fourTasks(page: Page): Promise<void> {
	for (const title of TITLES) {
		await tell(page, { type: 'jarvis/test/task_run', title, steps: ['a', 'b', 'c', 'd', 'e'], tick_ms: 20_000 });
	}
	const dock = page.getByTestId('task-dock');
	await expect(dock).toBeVisible({ timeout: 10_000 });
	for (const title of TITLES) await expect(dock).toContainText(title, { timeout: 10_000 });
}

const pageScrolls = (page: Page) =>
	page.evaluate(() => document.documentElement.scrollHeight > document.documentElement.clientHeight + 2);

test.afterEach(async ({ page }) => {
	await tell(page, { type: 'jarvis/test/task_reset' });
});

for (const viewport of [
	{ width: 1440, height: 900 },
	{ width: 1280, height: 720 }
]) {
	test(`four running tasks at ${viewport.width}×${viewport.height}: one line each, and the page does not scroll`, async ({ page }) => {
		await page.setViewportSize(viewport);
		await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
		await page.goto('/');
		await expect(page.getByTestId('reactor')).toBeVisible({ timeout: 15_000 });
		await fourTasks(page);
		await page.waitForTimeout(400);
		expect(await pageScrolls(page), 'the voice page scrolls because of the task dock').toBe(false);
		const rows = page.locator('[data-testid^="task-dock-row-"]');
		expect(await rows.count()).toBeGreaterThanOrEqual(4);
		for (const row of await rows.all()) {
			const box = await row.boundingBox();
			expect(box?.height ?? 0, 'a collapsed task row is more than one line tall').toBeLessThanOrEqual(44);
		}
		const list = await page.getByTestId('task-dock-list').boundingBox();
		expect(list?.height ?? 0, 'the list grows past its cap instead of scrolling inside itself').toBeLessThanOrEqual(viewport.height * 0.32 + 2);
	});
}

test('a click opens one task to its bar, sentence and steps; a second click folds it', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto('/');
	await expect(page.getByTestId('reactor')).toBeVisible({ timeout: 15_000 });
	await fourTasks(page);
	const row = page.locator('[data-testid^="task-dock-row-"]').first();
	const id = (await row.getAttribute('data-testid'))!.replace('task-dock-row-', '');
	await expect(page.getByTestId(`task-dock-detail-${id}`)).toHaveCount(0);
	await page.getByTestId(`task-dock-brief-${id}`).click();
	const detail = page.getByTestId(`task-dock-detail-${id}`);
	await expect(detail).toBeVisible();
	await expect(detail.getByRole('progressbar')).toBeVisible();
	await expect(detail.locator('ol.plan li')).toHaveCount(5);
	await expect(page.getByTestId(`task-dock-open-${id}`)).toHaveAttribute('href', `/work/tasks/${id}`);
	expect(await pageScrolls(page), 'one open task must not make the page scroll').toBe(false);
	await page.getByTestId(`task-dock-brief-${id}`).click();
	await expect(page.getByTestId(`task-dock-detail-${id}`)).toHaveCount(0);
});
