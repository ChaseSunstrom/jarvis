import { test, expect } from '@playwright/test';

/**
 * Watching one task should feel like watching Jarvis work.
 *
 * Before this, a running task was a bar and a title. Its tool calls arrived
 * when the job was over — a coding job that called nine tools over four minutes
 * showed nothing for four minutes — and the output of its checks arrived not at
 * all. The events exist now (`tests/contracts/task_events.json`) and this is
 * the page that renders them.
 *
 * What is asserted here is the thing that was missing: activity appearing WHILE
 * the task runs, not a summary after it stops.
 */

const openDetail = async (page: import('@playwright/test').Page) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto('/tasks');
	await expect(page.getByTestId('tasks-lede')).toBeVisible({ timeout: 15_000 });

	// A slow enough task that the page is open while it is still working.
	const taskId = await page.evaluate(async () => {
		const socket = new WebSocket(
			`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
		);
		await new Promise((resolve) => socket.addEventListener('open', resolve));
		const id = await new Promise<string>((resolve) => {
			socket.addEventListener('message', (event) => {
				const frame = JSON.parse(event.data as string);
				if (frame.result?.task_id) resolve(frame.result.task_id);
			});
			socket.send(
				JSON.stringify({
					id: 1,
					type: 'jarvis/test/task_run',
					kind: 'code',
					title: 'Add an OFFLINE state to the settings screen',
					steps: ['read the route', 'wrap it in ScreenState', 'run the check'],
					tick_ms: 900
				})
			);
		});
		socket.close();
		return id;
	});
	await page.goto(`/tasks/${taskId}`);
	await expect(page.getByTestId('task-detail')).toBeVisible({ timeout: 15_000 });
	return taskId;
};

test('a running task shows its tool calls as they happen', async ({ page }) => {
	await openDetail(page);

	// The call appears while it is still running — that is the point.
	const running = page.locator('[data-testid^="task-call-"]').first();
	await expect(running).toBeVisible({ timeout: 15_000 });
	await expect(page.getByText('running').first()).toBeVisible();

	// And then it finishes, with how long it took.
	await expect(page.getByText(/\d+ ms/).first()).toBeVisible({ timeout: 15_000 });
});

test('output arrives while the job is printing it', async ({ page }) => {
	await openDetail(page);
	const output = page.getByTestId('task-output');
	await expect(output).toBeVisible({ timeout: 15_000 });
	await expect(output).toContainText('working', { timeout: 15_000 });
});

test('the timeline says what happened, including before the page was open', async ({ page }) => {
	const taskId = await openDetail(page);
	const timeline = page.getByTestId('task-timeline');
	await expect(timeline).toBeVisible();
	await expect(timeline.getByTestId('timeline-entry').first()).toBeVisible({ timeout: 15_000 });

	// Reload mid-job: the log is replayed, so the page is not empty for somebody
	// who arrived late.
	await page.reload();
	await expect(page.getByTestId('task-detail')).toBeVisible({ timeout: 15_000 });
	await expect(
		page.getByTestId('task-timeline').getByTestId('timeline-entry').first(),
		'a page opened mid-job replays what it missed'
	).toBeVisible({ timeout: 15_000 });
});

test('cancel is where somebody watching would reach for it', async ({ page }) => {
	await openDetail(page);
	const cancel = page.getByTestId('task-cancel');
	await expect(cancel).toBeEnabled();
	await cancel.click();
	await expect(page.getByTestId('task-said')).toBeVisible({ timeout: 10_000 });
});

test('a task the list links to is the task the page shows', async ({ page }) => {
	const taskId = await openDetail(page);
	await page.goto('/tasks');
	await page.getByTestId(`task-open-${taskId}`).first().click();
	await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}$`));
	await expect(page.getByTestId('task-detail')).toBeVisible({ timeout: 15_000 });
});

test('a task shows the trace behind it — what ran, and what it cost', async ({ page }) => {
	/**
	 * M36's "view trace" link. The panel is on the task itself rather than on a
	 * page of its own, because the question it answers — why did this take
	 * eleven seconds — is asked while looking at the task.
	 */
	await openDetail(page);

	await expect(page.getByTestId('trace-summary')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('trace-summary')).toContainText('model call');
	await expect(page.getByTestId('trace-tokens')).toContainText('in');
	// Where the time actually went is the number people come for.
	await expect(page.getByTestId('trace-split')).toContainText('model');

	// The steps are collapsed: twelve spans of a research run should not be
	// the first thing on the page.
	await expect(page.getByTestId('trace-span-0')).toHaveCount(0);
	await page.getByTestId('trace-toggle').click();
	await expect(page.getByTestId('trace-span-0')).toBeVisible();
	// A failed span is visible AS failed, without opening anything else.
	await expect(page.getByTestId('trace-span-2')).toContainText('refused');
});
