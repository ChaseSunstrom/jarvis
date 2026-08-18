import { test, expect, type Page } from '@playwright/test';

/**
 * Long work, seen from the console.
 *
 * The claim under test is narrow and it is the whole feature: **a bar that
 * moves because the work moved.** Not a spinner, not a timer, not a number the
 * browser computed — jarvis-core fires `jarvis_task_updated` on every step and
 * these assertions only pass if that reaches the screen.
 *
 * The second claim is the one that is easy to get wrong and impossible to
 * notice: a task whose progress is genuinely unknowable must look DIFFERENT
 * from one that has done nothing. Both would render as an empty bar if the
 * console treated "no fraction" as zero, and both would look fine.
 */

/**
 * Send one frame to the mock over a socket of the test's own.
 *
 * A second socket is how the rest of this suite drives the backend: the page's
 * own client is not reachable from here, and reaching into it would test the
 * test rather than the console.
 */
async function tell(page: Page, frame: Record<string, unknown>): Promise<void> {
	await page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () => ws.send(JSON.stringify({ id: 91, ...payload }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		frame
	);
}

/** Ask the mock to run a task through its steps. */
const runTask = (page: Page, options: Record<string, unknown> = {}) =>
	tell(page, { type: 'jarvis/test/task_run', ...options });

/**
 * Empty the registry.
 *
 * The suite shares one mock process, so "there is nothing running" is only a
 * testable claim if a test can reach that state deliberately. Without this the
 * empty-state test passes or fails on whether the PREVIOUS test's job happened
 * to finish first — which it did, until a tick length changed.
 */
const resetTasks = (page: Page) => tell(page, { type: 'jarvis/test/task_reset' });

/** The tasks page, connected and past its skeleton. */
async function openTasks(page: Page): Promise<void> {
	await page.goto('/tasks');
	await expect(page.getByTestId('tasks-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
}

test('a task’s bar moves because the work moved', async ({ page }) => {
	await openTasks(page);
	await runTask(page, { title: 'Read twelve pages', steps: ['search', 'read', 'write up'], tick_ms: 250 });

	const card = page.locator('[data-testid^="task-card-"][data-kind="research"]').first();
	await expect(card).toBeVisible({ timeout: 10_000 });
	await expect(card).toContainText('Read twelve pages');

	const bar = card.getByRole('progressbar');

	// The bar starts empty and ends full, and it gets there by passing through
	// a value that is neither. Polling for a *strictly increasing* reading is
	// the only assertion that a spinner-on-a-timer would fail.
	const seen = new Set<string>();
	await expect
		.poll(
			async () => {
				const now = await bar.getAttribute('aria-valuenow');
				if (now) seen.add(now);
				return now;
			},
			{ timeout: 15_000, intervals: [60] }
		)
		.toBe('100');

	expect(
		[...seen].map(Number).filter((n) => n > 0 && n < 100).length,
		`the bar jumped straight to full — readings were ${[...seen].join(', ')}`
	).toBeGreaterThan(0);

	await expect(card).toHaveAttribute('data-status', 'done');
	await expect(card).toContainText('all twelve read');
});

test('an open-ended task is honestly indeterminate, not nought per cent', async ({ page }) => {
	// The bug this pins is silent in every screenshot: a crawl that cannot know
	// its own length would draw an empty bar sitting at 0% for the whole run,
	// indistinguishable from a task that never started. jarvis-core sends
	// `fraction: null` for exactly this, and the console has to draw it as a
	// different THING rather than as a different number.
	await openTasks(page);
	await runTask(page, {
		title: 'Crawl the archive',
		steps: ['first page', 'second page'],
		open_ended: true,
		tick_ms: 400
	});

	const card = page.locator('[data-testid^="task-card-"]', { hasText: 'Crawl the archive' }).first();
	await expect(card).toBeVisible({ timeout: 10_000 });
	const bar = card.getByRole('progressbar');

	await expect(bar).toHaveAttribute('data-mode', 'indeterminate', { timeout: 10_000 });
	// No number, which is what makes a screen reader say "busy" instead of
	// reading out a figure nobody computed.
	await expect(bar).not.toHaveAttribute('aria-valuenow', /.*/);
	await expect(bar).toHaveAttribute('aria-busy', 'true');

	// And it still resolves to a real 100 when the work is actually over.
	await expect(bar).toHaveAttribute('aria-valuenow', '100', { timeout: 15_000 });
});

test('a failure keeps the ground it covered', async ({ page }) => {
	// How far it got is the only interesting fact about a failed job. Snapping
	// it to 0 or to 100 throws that away, and both look tidy.
	await openTasks(page);
	await runTask(page, {
		title: 'Fetch the index',
		steps: ['connect', 'read', 'parse', 'store'],
		fail_at: 1,
		tick_ms: 200
	});

	const card = page.locator('[data-testid^="task-card-"]', { hasText: 'Fetch the index' }).first();
	await expect(card).toHaveAttribute('data-status', 'error', { timeout: 15_000 });
	await expect(card).toContainText('the model server refused');

	const value = Number(await card.getByRole('progressbar').getAttribute('aria-valuenow'));
	expect(value, 'a failure was reported as complete').toBeLessThan(100);
	expect(value, 'a failure lost the ground it covered').toBeGreaterThan(0);
});

test('the dock follows you off the page, HUD included', async ({ page }) => {
	// A research run started from the orb is still going three navigations
	// later, and the dock is the only thing on any surface that says so.
	await openTasks(page);
	await runTask(page, { title: 'A long errand', steps: ['a', 'b', 'c', 'd'], tick_ms: 900 });
	await expect(page.getByTestId('task-dock')).toBeVisible({ timeout: 10_000 });

	await page.goto('/devices');
	await expect(page.getByTestId('task-dock')).toContainText('A long errand', { timeout: 15_000 });

	await page.goto('/');
	await expect(page.getByTestId('task-dock')).toContainText('A long errand', { timeout: 15_000 });
	// Its bar is live on the HUD too, not a static row.
	await expect(page.getByTestId('task-dock').getByRole('progressbar').first()).toBeVisible();
});

test('cancelling says what it actually did', async ({ page }) => {
	// jarvis-core's registry is a record, not a scheduler: it cannot reach into
	// the coroutine. Saying a flat "Cancelled" over work that may still be
	// running is the same lie the registry was built to stop telling.
	await openTasks(page);
	await runTask(page, { title: 'Something slow', steps: ['a', 'b', 'c'], tick_ms: 2000 });

	const card = page.locator('[data-testid^="task-card-"]', { hasText: 'Something slow' }).first();
	await expect(card).toBeVisible({ timeout: 10_000 });
	await card.getByRole('button', { name: 'CANCEL' }).click();

	await expect(card).toHaveAttribute('data-status', 'cancelled', { timeout: 10_000 });
	await expect(page.getByTestId('toast').first()).toContainText('may still be running', {
		timeout: 10_000
	});
});

test('forgetting a task takes the row with it, and clearing takes the finished ones', async ({
	page
}) => {
	await openTasks(page);
	await runTask(page, { title: 'Disposable', steps: ['a'], tick_ms: 80 });

	const card = page.locator('[data-testid^="task-card-"]', { hasText: 'Disposable' }).first();
	await expect(card).toHaveAttribute('data-status', 'done', { timeout: 15_000 });
	await card.getByRole('button', { name: 'FORGET' }).click();
	await expect(page.locator('[data-testid^="task-card-"]', { hasText: 'Disposable' })).toHaveCount(0, {
		timeout: 10_000
	});

	await runTask(page, { title: 'Also finished', steps: ['a'], tick_ms: 80 });
	await expect(
		page.locator('[data-testid^="task-card-"]', { hasText: 'Also finished' }).first()
	).toHaveAttribute('data-status', 'done', { timeout: 15_000 });
	await page.getByTestId('clear-finished').click();
	await expect(page.getByTestId('tasks-finished')).toHaveCount(0, { timeout: 10_000 });
});

test('the page says so plainly when there is nothing running', async ({ page }) => {
	await openTasks(page);
	await resetTasks(page);
	await expect(page.getByTestId('tasks-empty')).toBeVisible({ timeout: 10_000 });
	await expect(page.getByTestId('tasks-empty')).toContainText('Nothing running');
	// No dock either: an empty strip on every page would be chrome that never
	// pays for itself.
	await expect(page.getByTestId('task-dock')).toHaveCount(0);
});

test('TASKS is reachable by nav, chord and palette', async ({ page }) => {
	await page.goto('/devices');
	await expect(page.getByTestId('nav-tasks')).toBeVisible({ timeout: 15_000 });

	await page.getByTestId('nav-tasks').click();
	await expect(page.getByTestId('route')).toHaveAttribute('data-route', '/tasks');

	// Waited on, not merely navigated to: `page.goto` resolves on load and the
	// chord listener is attached during hydration, so keys pressed straight
	// after a goto reach nothing. `data-redialling="false"` needs the page's
	// socket, which needs hydration.
	await page.goto('/devices');
	await expect(page.getByTestId('devices-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
	await page.keyboard.press('g');
	await page.keyboard.press('k');
	await expect(page.getByTestId('route')).toHaveAttribute('data-route', '/tasks', {
		timeout: 10_000
	});

	await page.goto('/devices');
	await page.getByTestId('palette-open').click();
	await page.getByTestId('palette-input').fill('tasks');
	// Clicked by id rather than by pressing Enter on whatever ranked first: the
	// index also holds every entity, and this test is about the page being IN
	// the palette, not about how it scores against a lamp called "task".
	await page.getByTestId('palette-item-page:/tasks').click();
	await expect(page.getByTestId('route')).toHaveAttribute('data-route', '/tasks', {
		timeout: 10_000
	});
});
