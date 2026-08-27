import { test, expect, type Page } from '@playwright/test';

/**
 * M99 — the console keeps up.
 *
 * Five things the console audit of 27 Aug found it did not do: retry a task
 * that failed (the server could, the console had no button), show a room
 * being made while the Areas page is open, move a scheduled job's row when it
 * fires, follow a setting changed elsewhere, and give a phone a room. Each is
 * proved against the mock the way the real house would do it — a frame over a
 * second socket, then the screen, with no reload in between.
 */

async function tell(page: Page, frame: Record<string, unknown>): Promise<unknown> {
	return page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () => ws.send(JSON.stringify({ id: 99, ...payload }));
				ws.onmessage = (event) => {
					const frame = JSON.parse(String(event.data));
					if (frame.id === 99) {
						ws.close();
						resolve(frame.result ?? frame.error ?? null);
					}
				};
			}),
		frame
	);
}

test.beforeEach(async ({ page }) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
});

test('a task that failed is retried from its card, and comes back', async ({ page }) => {
	await page.goto('/tasks');
	await expect(page.getByTestId('tasks-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
	await tell(page, {
		type: 'jarvis/test/task_run',
		title: 'Index the handbook again',
		steps: ['fetch', 'parse', 'store'],
		fail_at: 1,
		tick_ms: 120
	});
	const card = page.locator('[data-testid^="task-card-"]', { hasText: 'Index the handbook again' }).first();
	await expect(card).toHaveAttribute('data-status', 'error', { timeout: 15_000 });
	const id = (await card.getAttribute('data-testid'))!.replace('task-card-', '');

	// The button is there for a failure and for nothing else.
	await expect(page.getByTestId(`task-retry-${id}`)).toBeVisible();
	await page.getByTestId(`task-retry-${id}`).click();
	await expect(card).not.toHaveAttribute('data-status', 'error', { timeout: 5_000 });
	await expect(card).toHaveAttribute('data-status', 'done', { timeout: 15_000 });
	await expect(page.getByTestId(`task-retry-${id}`)).toHaveCount(0);
});

test('the task page retries too, and says so', async ({ page }) => {
	await page.goto('/tasks');
	await expect(page.getByTestId('tasks-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
	const started = (await tell(page, {
		type: 'jarvis/test/task_run',
		title: 'Read the register',
		steps: ['open', 'read'],
		fail_at: 1,
		tick_ms: 120
	})) as { task_id: string };
	await page.goto(`/work/tasks/${started.task_id}`);
	const retry = page.getByTestId('task-retry');
	await expect(retry).toBeEnabled({ timeout: 15_000 });
	await retry.click();
	await expect(page.getByTestId('task-said')).toContainText('Back on the queue');
	await expect(retry).toBeDisabled({ timeout: 15_000 });
});

test('a room made elsewhere appears on Areas without a reload', async ({ page }) => {
	await page.goto('/house/areas');
	await expect(page.getByTestId('areas-screen')).toBeVisible({ timeout: 15_000 });
	await tell(page, { type: 'config/area_registry/create', name: 'Pantry' });
	await expect(page.getByTestId('area-pantry')).toBeVisible({ timeout: 10_000 });
});

test('a scheduled job that fires moves its row without a reload', async ({ page }) => {
	await page.goto('/tasks');
	await expect(page.getByTestId('tasks-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
	await tell(page, { type: 'jarvis/test/schedule_reset' });
	const row = page.getByTestId('sched-row-brief');
	await expect(row).toBeVisible({ timeout: 15_000 });
	await tell(page, { type: 'jarvis/test/schedule_fire', job_id: 'brief', result: 'told you: the M99 brief' });
	await expect(row).toContainText('the M99 brief', { timeout: 10_000 });
});

test('a setting changed by another client moves the open row', async ({ page }) => {
	await page.goto('/settings/house');
	await expect(page.getByTestId('everything-summary')).toBeVisible({ timeout: 15_000 });
	await page.getByTestId('everything-summary').click();
	const input = page.getByTestId('input-jarvis.language');
	await expect(input).toBeVisible({ timeout: 15_000 });
	const before = await input.inputValue();
	const next = before === 'en' ? 'fr' : 'en';
	await tell(page, { type: 'config/settings/set', key: 'jarvis.language', value: next });
	await expect(input).toHaveValue(next, { timeout: 10_000 });
	await tell(page, { type: 'config/settings/set', key: 'jarvis.language', value: before });
});

test('a phone gets a room on Devices, through its registry entry', async ({ page }) => {
	await page.goto('/house/devices');
	const picker = page.getByTestId('companion-area-pixel-8');
	await expect(picker).toBeVisible({ timeout: 15_000 });
	const options = await picker.locator('option').allTextContents();
	expect(options.length).toBeGreaterThan(1);
	const roomId = await picker.locator('option').nth(1).getAttribute('value');
	await picker.selectOption(roomId!);
	await expect(page.getByTestId('toast').first()).toContainText('is in the', { timeout: 10_000 });
	// The list row carries it now, and a reload agrees.
	await page.reload();
	await expect(page.getByTestId('companion-area-pixel-8')).toHaveValue(roomId!, { timeout: 15_000 });
});
