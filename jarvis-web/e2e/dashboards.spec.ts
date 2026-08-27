import { test, expect, type Page } from '@playwright/test';

/**
 * Dashboards a person arranges, and the promise that the arrangement sticks.
 *
 * The failure this suite is written against is the one that makes a dashboard
 * feature worthless: you spend ten minutes laying out six graphs, reload, and
 * find the default back. So the load-bearing test here is not "a chart drew" —
 * it is "what I changed is still there after a reload".
 */

const open = async (page: import('@playwright/test').Page) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto('/dashboards');
	await expect(page.getByTestId('dashboards-screen')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('dashboard-grid')).toBeVisible({ timeout: 15_000 });
};
/** Into the layout editor and out (M55): + Widget opens it, DONE closes it. */
async function toggleEdit(page: Page) {
	const add = page.getByTestId('dashboard-add');
	if (await add.isVisible()) await add.click();
	else await toggleEdit(page);
}

test('every chart type draws something, and a gap stays a gap', async ({ page }) => {
	await open(page);
	// The console opens on the House (M63); the graphs are on the Homelab and
	// on mine. Homelab carries a line and a number; mine carries bars and a gauge.
	await page.getByTestId('dashboard-picker').selectOption('homelab');
	await expect(page.locator('[data-type="line"]').first()).toBeVisible({ timeout: 10_000 });
	await expect(page.locator('[data-type="stat"] [data-testid="chart-value"]').first()).toBeVisible();

	await page.getByTestId('dashboard-picker').selectOption('mine');
	await expect(page.locator('[data-type="bar"]').first()).toBeVisible({ timeout: 10_000 });
	await expect(page.locator('[data-type="gauge"]').first()).toBeVisible();

	// The mock sends one null in the middle of every series. A line drawn
	// through it would be a claim about a period nothing was recorded in.
	await page.getByTestId('dashboard-picker').selectOption('homelab');
	const path = page.locator('[data-type="line"] path.line').first();
	await expect(path).toBeVisible({ timeout: 10_000 });
	const d = (await path.getAttribute('d')) ?? '';
	expect(d.match(/M/g)?.length ?? 0, 'the line should break at the gap').toBeGreaterThan(1);
});

test('a widget can be added, resized, moved and removed — and it stays that way', async ({
	page
}) => {
	await open(page);
	await page.getByTestId('dashboard-picker').selectOption('mine');
	await expect(page.getByTestId('widget-w1')).toBeVisible({ timeout: 10_000 });

	await toggleEdit(page);

	// Add.
	await page.getByTestId('new-series').fill('jarvis.turns');
	await page.getByTestId('new-title').fill('Turns');
	await page.getByTestId('new-widget').click();
	const added = page.getByTestId('widget-w3');
	await expect(added).toBeVisible({ timeout: 10_000 });

	// Resize.
	const before = Number(await added.getAttribute('data-w'));
	await page.getByTestId('wider-w3').click();
	await expect(added).toHaveAttribute('data-w', String(before + 1), { timeout: 10_000 });

	// Move.
	const x = Number(await added.getAttribute('data-x'));
	await page.getByTestId('right-w3').click();
	await expect(added).toHaveAttribute('data-x', String(x + 1), { timeout: 10_000 });

	// The whole point: it survives a reload, because it was saved.
	await page.reload();
	await page.getByTestId('dashboard-picker').selectOption('mine');
	const again = page.getByTestId('widget-w3');
	await expect(again, 'the layout did not persist').toBeVisible({ timeout: 15_000 });
	await expect(again).toHaveAttribute('data-w', String(before + 1));

	// Remove.
	await toggleEdit(page);
	await page.getByTestId('remove-w3').click();
	await expect(again).toHaveCount(0, { timeout: 10_000 });
});

test('reordering swaps two widgets, so nothing is left in a gap', async ({ page }) => {
	await open(page);
	await page.getByTestId('dashboard-picker').selectOption('mine');
	await toggleEdit(page);

	const first = page.getByTestId('widget-w1');
	const second = page.getByTestId('widget-w2');
	const firstX = Number(await first.getAttribute('data-x'));
	const secondX = Number(await second.getAttribute('data-x'));
	expect(firstX).not.toBe(secondX);

	await first.dragTo(second);
	await expect(first).toHaveAttribute('data-x', String(secondX), { timeout: 10_000 });
	await expect(second).toHaveAttribute('data-x', String(firstX));
});

test('a shipped dashboard cannot be edited, and says so', async ({ page }) => {
	await open(page);
	await page.getByTestId('dashboard-picker').selectOption('homelab');
	await expect(page.getByText('shipped · read only')).toBeVisible();
	await expect(page.getByTestId('dashboard-edit')).toHaveCount(0);
});

test('the range switch asks the backend for a different window', async ({ page }) => {
	await open(page);
	const asked: string[] = [];
	await page.routeWebSocket(/\/ws$/, (ws) => {
		const server = ws.connectToServer();
		ws.onMessage((message) => {
			try {
				const frame = JSON.parse(String(message));
				if (frame.type === 'jarvis/metrics/query') asked.push(String(frame.range));
			} catch {
				/* binary audio frames are not JSON */
			}
			server.send(message);
		});
		server.onMessage((message) => ws.send(message));
	});
	await page.reload();
	await expect(page.getByTestId('dashboard-grid')).toBeVisible({ timeout: 15_000 });
	// The House has no graph to ask for; the Homelab does.
	await page.getByTestId('dashboard-picker').selectOption('homelab');
	await expect(page.locator('[data-type="line"]').first()).toBeVisible({ timeout: 10_000 });
	await page.getByTestId('range-24h').click();
	await expect.poll(() => asked.includes('24h'), { timeout: 10_000 }).toBe(true);
});

// --- the dashboard shows the house (M63) --------------------------------------
//
// The House is what the console opens on: one widget of each kind, drawn from
// the mock's own state, sensors, cameras, sky and moments. The load-bearing
// tests are the ones a screenshot cannot make: a press on a tile reaches the
// backend as the same `call_service` the Devices row sends and the tile changes
// only when the backend says so; a camera set to consent: never shows its
// refusal and no frame; a moment landing live goes to the top.

/** A word to the mock over its own socket (the same hook the other specs use). */
async function tell(page: Page, payload: Record<string, unknown>) {
	await page.evaluate(
		(frame) =>
			new Promise((resolve) => {
				const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
				ws.onopen = () => ws.send(JSON.stringify({ id: 91, ...frame }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		payload
	);
}

test('the console opens on the House: a tile, the readings by room, a still, the sky, the moments', async ({ page }) => {
	await open(page);
	await expect(page.getByTestId('dashboard-picker')).toHaveValue('house');
	await expect(page.getByText('shipped · read only')).toBeVisible();
	for (const kind of ['entity', 'readings', 'camera', 'sky', 'moments']) {
		await expect(page.locator(`[data-kind="${kind}"]`).first(), `no ${kind} widget`).toBeVisible();
	}

	// The tile: the light's state, when it changed, and its one switch.
	await expect(page.getByTestId('tile-state-light.hall_lamp')).toHaveText(/^\s*on\s*$/);
	await expect(page.getByTestId('toggle-light.hall_lamp')).toHaveText('TURN OFF');

	// The readings, grouped by the rooms the registry puts them in.
	const rooms = page.getByTestId('readings-room');
	await expect(rooms).toHaveCount(3, { timeout: 10_000 });
	await expect(page.getByTestId('reading-sensor.garage_humidity')).toContainText('61 %');
	await expect(page.locator('[data-testid="readings-room"][aria-label="Garage"]')).toContainText('Garage Humidity');
	await expect(page.locator('[data-testid="readings-room"][aria-label="Lab"]')).toContainText('Lab Temperature');

	// The sky, from the mock's cached elements.
	await expect(page.getByTestId('sky-pass')).toContainText('ISS next rises');
	await expect(page.getByTestId('sky-pass')).toContainText('21:14');
	await expect(page.getByTestId('sky-pass')).toContainText('up to 41°');
	await expect(page.getByTestId('sky-moon')).toContainText('waxing gibbous');

	// The moments, newest first.
	const moments = page.locator('[data-testid^="moment-"]');
	await expect(moments).toHaveCount(2);
	await expect(moments.first()).toContainText('Finished: research on heat pumps');
});

test('a press on an entity tile round-trips call_service, and the tile follows the backend', async ({ page }) => {
	// Every frame the page sends, so the press can be matched against the exact
	// call_service the Devices row would send — not merely "something changed".
	const sent: Record<string, unknown>[] = [];
	await page.routeWebSocket(/\/ws$/, (ws) => {
		const server = ws.connectToServer();
		ws.onMessage((message) => {
			try {
				sent.push(JSON.parse(String(message)));
			} catch {
				/* binary audio frames are not JSON */
			}
			server.send(message);
		});
		server.onMessage((message) => ws.send(message));
	});
	await open(page);
	await expect(page.getByTestId('tile-state-light.hall_lamp')).toHaveText(/^\s*on\s*$/);
	await page.getByTestId('toggle-light.hall_lamp').click();

	// The same frame the Devices row sends: light.turn_off against this entity.
	await expect
		.poll(() =>
			sent
				.filter((frame) => frame.type === 'call_service')
				.map((frame) => `${frame.domain}.${frame.service}:${(frame.service_data as { entity_id?: string })?.entity_id}`)
		)
		.toContain('light.turn_off:light.hall_lamp');
	// And the tile changed because the backend said so: the mock only mutates
	// through call_service, and the page only redraws on state_changed.
	await expect(page.getByTestId('tile-state-light.hall_lamp')).toHaveText(/^\s*off\s*$/, { timeout: 10_000 });
	await expect(page.getByTestId('toggle-light.hall_lamp')).toHaveText('TURN ON');

	// Put it back, so the next test meets the seed.
	await page.getByTestId('toggle-light.hall_lamp').click();
	await expect(page.getByTestId('tile-state-light.hall_lamp')).toHaveText(/^\s*on\s*$/, { timeout: 10_000 });
});

test('a camera without consent shows its refusal and no frame', async ({ page }) => {
	await open(page);
	const why = page.getByTestId('camera-why');
	await expect(why).toBeVisible({ timeout: 10_000 });
	await expect(why).toHaveAttribute('data-decision', 'policy_never');
	await expect(why).toContainText('consent: never');
	await expect(page.locator('[data-kind="camera"] img')).toHaveCount(0);
});

test('a moment landing live goes to the top of the moments widget', async ({ page }) => {
	await open(page);
	await expect(page.locator('[data-testid^="moment-"]')).toHaveCount(2);
	await tell(page, { type: 'jarvis/test/moment', kind: 'reminder', title: 'Check the oven' });
	const moments = page.locator('[data-testid^="moment-"]');
	await expect(moments).toHaveCount(3, { timeout: 10_000 });
	await expect(moments.first()).toContainText('Check the oven');
	await expect(moments.first()).toHaveAttribute('data-kind', 'reminder');
});

test('a reading changing live changes the row, not the page', async ({ page }) => {
	await open(page);
	await expect(page.getByTestId('reading-sensor.lab_temperature')).toContainText('21.4 °C');
	await tell(page, { type: 'jarvis/test/sensor_change', entity_id: 'sensor.lab_temperature', value: '22.8' });
	await expect(page.getByTestId('reading-sensor.lab_temperature')).toContainText('22.8 °C', { timeout: 10_000 });
	await expect(page.getByTestId('reading-sensor.lab_temperature')).toContainText('just now');
});

test('the kind picker: a tile, a camera and the sky can be added to a dashboard you own, and they stay', async ({ page }) => {
	await open(page);
	await page.getByTestId('dashboard-picker').selectOption('mine');
	await expect(page.getByTestId('widget-w1')).toBeVisible({ timeout: 10_000 });
	await toggleEdit(page);

	// The kind first; the fields follow the kind.
	await expect(page.getByTestId('new-series')).toBeVisible();
	await page.getByTestId('new-kind').selectOption('entity');
	await expect(page.getByTestId('new-series')).toHaveCount(0);
	// A name is not an entity id, and the editor says so rather than saving a tile about nothing.
	await page.getByTestId('new-entity').fill('desk fan');
	await page.getByTestId('new-widget').click();
	await expect(page.getByTestId('dashboard-said')).toContainText('not an entity id');
	await page.getByTestId('new-entity').fill('switch.desk_fan');
	await page.getByTestId('new-widget').click();
	const tile = page.getByTestId('widget-w3');
	await expect(tile).toBeVisible({ timeout: 10_000 });
	await expect(tile).toHaveAttribute('data-kind', 'entity');
	await expect(page.getByTestId('toggle-switch.desk_fan')).toHaveText('TURN ON');

	// A camera that always answers: the frame arrives as an image.
	await page.getByTestId('new-kind').selectOption('camera');
	await page.getByTestId('new-camera').fill('Garden');
	await page.getByTestId('new-widget').click();
	await expect(page.getByTestId('widget-w4')).toHaveAttribute('data-kind', 'camera', { timeout: 10_000 });
	await expect(page.locator('[data-testid="widget-w4"] img')).toBeVisible({ timeout: 10_000 });

	await page.getByTestId('new-kind').selectOption('sky');
	await page.getByTestId('new-widget').click();
	await expect(page.getByTestId('widget-w5')).toHaveAttribute('data-kind', 'sky', { timeout: 10_000 });

	// The whole point: they survive a reload, because they were saved with their kind.
	await page.reload();
	await page.getByTestId('dashboard-picker').selectOption('mine');
	await expect(page.getByTestId('widget-w3')).toHaveAttribute('data-kind', 'entity', { timeout: 15_000 });
	await expect(page.getByTestId('widget-w4')).toHaveAttribute('data-kind', 'camera');
	await expect(page.getByTestId('widget-w5')).toHaveAttribute('data-kind', 'sky');

	// Leave mine as it was found.
	await toggleEdit(page);
	for (const id of ['w3', 'w4', 'w5']) {
		await page.getByTestId(`remove-${id}`).click();
		await expect(page.getByTestId(`widget-${id}`)).toHaveCount(0, { timeout: 10_000 });
	}
});
