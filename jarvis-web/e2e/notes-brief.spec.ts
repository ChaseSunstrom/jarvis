import { expect, test, type Page } from '@playwright/test';

/**
 * M112 — the operator's report of 27 Aug 2026: "all of the notes popups
 * are still taking up a ton of space on the voice screen". Three long
 * notes put up by Jarvis: each is one row of the surface's grid (a title
 * and a first line), the page does not scroll, ⤢ opens one to the whole
 * note as written, ⤡ folds it back.
 */
const tell = (page: Page, frame: Record<string, unknown>) =>
	page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
				ws.onopen = () => ws.send(JSON.stringify({ id: 92, ...payload }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		frame
	);

const LONG = [
	'# Sensor audit\n\nEvery sensor in the house, read at 06:00.\n\n- Garage: 21.5 °C\n- Kitchen: 22.1 °C\n- Hall: 20.8 °C\n\n## Anomalies\n\nNone today. The garage sensor was announced at 05:58 and read within the minute.',
	'# Cheap-rate report\n\nThe cheap rate runs 00:30–04:30.\n\n1. Dishwasher at 00:40\n2. Washer at 01:10\n3. Car at 02:00\n\nEverything finished before 04:00.',
	'# Front door\n\nOpened twice this morning: 07:12 and 08:40. Nobody unknown at the door.'
];

async function threeNotes(page: Page): Promise<string[]> {
	const ids: string[] = [];
	for (const [i, text] of LONG.entries()) {
		await tell(page, { type: 'jarvis/test/surface_show', kind: 'note', title: `Note ${i + 1}`, text });
	}
	await expect(page.getByTestId('surface')).toHaveAttribute('data-count', '3', { timeout: 10_000 });
	for (const panel of await page.locator('[data-testid^="surface-panel-"]').all()) {
		ids.push((await panel.getAttribute('data-testid'))!.replace('surface-panel-', ''));
	}
	return ids;
}

test.beforeEach(async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto('/');
	await expect(page.getByTestId('reactor')).toBeVisible({ timeout: 15_000 });
});

// `force` on the ⤢ clicks: on CI the panel's enter animation kept the button
// "not stable" for the whole timeout while the click itself lands fine (it does
// here, unforced); what the case proves is the state after the click.
test.afterEach(async ({ page }) => {
	await tell(page, { type: 'jarvis/surface/clear' }).catch(() => {});
});

test('three long notes are three one-row briefs; the page does not scroll', async ({ page }) => {
	const ids = await threeNotes(page);
	const row = (await page.getByTestId('surface').boundingBox())!.width / 12;
	for (const id of ids) {
		const box = (await page.getByTestId(`surface-panel-${id}`).boundingBox())!;
		expect(box.height, `note ${id} is taller than one row (${Math.round(box.height)} px, a row is ${Math.round(row)} px)`).toBeLessThanOrEqual(row + 2);
		await expect(page.getByTestId(`surface-brief-${id}`)).toBeVisible();
		await expect(page.getByTestId(`surface-text-${id}`)).toHaveCount(0);
	}
	const brief = await page.getByTestId(`surface-brief-${ids[0]}`).textContent();
	expect(brief?.trim()).toBe('Sensor audit');
	expect(await page.evaluate(() => document.documentElement.scrollHeight > document.documentElement.clientHeight + 2)).toBe(false);
});

test('⤢ opens one note to the whole text as written, and ⤡ folds it back to a line', async ({ page }) => {
	const ids = await threeNotes(page);
	const id = ids[0];
	await page.getByTestId(`surface-open-${id}`).click({ force: true });
	const text = page.getByTestId(`surface-text-${id}`);
	await expect(text).toBeVisible({ timeout: 5_000 });
	await expect(text.locator('h1')).toHaveText('Sensor audit');
	await expect(text.locator('li')).toHaveCount(3);
	const row = (await page.getByTestId('surface').boundingBox())!.width / 12;
	const open = (await page.getByTestId(`surface-panel-${id}`).boundingBox())!;
	expect(open.height).toBeGreaterThan(row * 3);
	await page.getByTestId(`surface-open-${id}`).click({ force: true });
	await expect(page.getByTestId(`surface-brief-${id}`)).toBeVisible({ timeout: 5_000 });
	const folded = (await page.getByTestId(`surface-panel-${id}`).boundingBox())!;
	expect(folded.height).toBeLessThanOrEqual(row + 2);
});
