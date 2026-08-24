import { test, expect } from '@playwright/test';

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

test('every chart type draws something, and a gap stays a gap', async ({ page }) => {
	await open(page);
	// The shipped board carries a line and a number; mine carries bars and a gauge.
	await expect(page.locator('[data-type="line"]').first()).toBeVisible();
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

	await page.getByTestId('dashboard-edit').click();

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
	await page.getByTestId('dashboard-edit').click();
	await page.getByTestId('remove-w3').click();
	await expect(again).toHaveCount(0, { timeout: 10_000 });
});

test('reordering swaps two widgets, so nothing is left in a gap', async ({ page }) => {
	await open(page);
	await page.getByTestId('dashboard-picker').selectOption('mine');
	await page.getByTestId('dashboard-edit').click();

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
	await page.getByTestId('range-24h').click();
	await expect.poll(() => asked.includes('24h'), { timeout: 10_000 }).toBe(true);
});
