import { expect, test, type Page } from '@playwright/test';

// M83 — pull things up. "Show me the front door" puts a panel on the voice
// screen beside the instrument; a person drags it, closes it, or says "clear
// the screen". The mock plays the server: `jarvis/test/surface_show` is what
// the model's `show` tool does, and every change comes back as
// `jarvis_surface_changed`, so the screen draws what the house holds and not
// what it last asked for.

const tell = (page: Page, frame: Record<string, unknown>) =>
	page.evaluate(
		(payload) =>
			new Promise<unknown>((resolve) => {
				const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
				ws.onopen = () => ws.send(JSON.stringify({ id: 93, ...payload }));
				ws.onmessage = (m) => {
					ws.close();
					resolve(JSON.parse(String(m.data)));
				};
			}),
		frame
	);

test.beforeEach(async ({ page }) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/');
	await expect(page.getByTestId('reactor')).toBeVisible({ timeout: 15_000 });
	await tell(page, { type: 'jarvis/surface/clear' });
});

test('a shown entity and a shown camera appear beside the instrument, live, and leave on ×', async ({ page }) => {
	await tell(page, { type: 'jarvis/test/surface_show', kind: 'entity', entity: 'light.hall_lamp', title: 'Hall lamp' });
	const lamp = page.locator('[data-testid^="surface-panel-"][data-kind="entity"]').first();
	await expect(lamp).toBeVisible({ timeout: 10_000 });
	await expect(lamp).toContainText(/hall lamp/i);
	await expect(page.getByTestId('surface')).toHaveAttribute('data-count', '1');

	await tell(page, { type: 'jarvis/test/surface_show', kind: 'camera', camera: 'front door', title: 'Front door' });
	const cam = page.locator('[data-testid^="surface-panel-"][data-kind="camera"]').first();
	await expect(cam).toBeVisible({ timeout: 10_000 });
	await expect(page.getByTestId('surface')).toHaveAttribute('data-count', '2');

	// Beside the instrument, not over it: the two panels do not overlap the reactor's box.
	const reactor = await page.getByTestId('reactor').boundingBox();
	for (const panel of [lamp, cam]) {
		const box = await panel.boundingBox();
		expect(box && reactor).toBeTruthy();
		const overlaps =
			box!.x < reactor!.x + reactor!.width && box!.x + box!.width > reactor!.x &&
			box!.y < reactor!.y + reactor!.height && box!.y + box!.height > reactor!.y;
		expect(overlaps, 'a panel covers the instrument').toBe(false);
	}

	const camId = (await cam.getAttribute('data-testid'))!.replace('surface-panel-', '');
	await page.getByTestId(`surface-close-${camId}`).click();
	await expect(cam).toHaveCount(0);
	await expect(page.getByTestId('surface')).toHaveAttribute('data-count', '1');
});

test('a drag moves a panel on the grid and the server is told where it landed', async ({ page }) => {
	await tell(page, { type: 'jarvis/test/surface_show', kind: 'sky', title: 'Sky', x: 0, y: 0 });
	const panel = page.locator('[data-testid^="surface-panel-"][data-kind="sky"]').first();
	await expect(panel).toBeVisible({ timeout: 10_000 });
	const id = (await panel.getAttribute('data-testid'))!.replace('surface-panel-', '');
	const box = (await panel.boundingBox())!;
	const surface = (await page.getByTestId('surface').boundingBox())!;
	const column = surface.width / 12;

	// Drag it four columns right and two rows down, by the header.
	await page.mouse.move(box.x + 40, box.y + 12);
	await page.mouse.down();
	await page.mouse.move(box.x + 40 + column * 2, box.y + 12 + column, { steps: 6 });
	await page.mouse.move(box.x + 40 + column * 4, box.y + 12 + column * 2, { steps: 6 });
	await page.mouse.up();

	await expect(panel).toHaveAttribute('data-x', '4');
	await expect(panel).toHaveAttribute('data-y', '2');
	const moves = (await tell(page, { type: 'jarvis/test/surface_moves' })) as { result?: { moves?: { id: string; x: number; y: number }[] } };
	const mine = (moves.result?.moves ?? []).filter((m) => m.id === id);
	expect(mine.length, 'the drop was not told to the server').toBeGreaterThan(0);
	expect(mine[mine.length - 1]).toMatchObject({ x: 4, y: 2 });

	// Cleared from elsewhere — the assistant, another screen — it goes.
	await tell(page, { type: 'jarvis/surface/clear' });
	await expect(panel).toHaveCount(0, { timeout: 10_000 });
	await expect(page.getByTestId('surface')).toHaveAttribute('data-count', '0');
});

test("a sensor's history draws as a chart, in the sensor's unit", async ({ page }) => {
	await tell(page, { type: 'jarvis/test/surface_show', kind: 'chart', entity: 'sensor.lab_temperature', title: 'Lab temperature' });
	const panel = page.locator('[data-testid^="surface-panel-"][data-kind="chart"]').first();
	await expect(panel).toBeVisible({ timeout: 10_000 });
	await expect(panel.locator('svg').first()).toBeVisible({ timeout: 10_000 });
	await expect(panel.locator('svg path').first()).toBeVisible();
	await tell(page, { type: 'jarvis/surface/clear' });
});
