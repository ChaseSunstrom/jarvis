import { test, expect, type Page } from '@playwright/test';

/**
 * What Jarvis will do later, from the console.
 *
 * On `/tasks` deliberately: a scheduled job and the task it mints are the same
 * thing at two moments, and putting them a navigation apart would make "did my
 * seven o'clock reminder run?" a two-page question.
 *
 * The console can schedule a SERVICE call, which the assistant's own tool
 * cannot — a request from here carried a bearer token, a tool call may have
 * been shaped by a page the model read. That asymmetry is jarvis-core's, and
 * what this file checks is that the console actually offers the third kind.
 */

async function tell(page: Page, frame: Record<string, unknown>): Promise<void> {
	await page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () => ws.send(JSON.stringify({ id: 61, ...payload }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		frame
	);
}

async function openTasks(page: Page): Promise<void> {
	await page.goto('/tasks');
	await expect(page.getByTestId('schedule-panel')).toBeVisible({ timeout: 15_000 });
	// The suite shares one mock process. Without this, "no job matches" is a
	// claim that passes only for whichever test happens to run first.
	await tell(page, { type: 'jarvis/test/schedule_reset' });
	await page.reload();
	await expect(page.getByTestId('schedule-panel')).toBeVisible({ timeout: 15_000 });
}

/** The row for a job with this text in it, rather than "the first row". */
function rowFor(page: Page, text: string) {
	return page.locator('[data-testid^="sched-row-job-"]', { hasText: text }).first();
}

test('a reminder can be scheduled, and says when it next runs', async ({ page }) => {
	await openTasks(page);
	await page.getByTestId('sched-new').click();
	await page.locator('#sched-message').fill('Take the bins out');
	await page.getByTestId('sched-mode').selectOption('daily');
	await page.locator('#sched-time').fill('19:00');
	await page.getByTestId('sched-save').click();

	const row = rowFor(page, 'Take the bins out');
	await expect(row).toBeVisible({ timeout: 10_000 });
	// The sentence comes from the SERVER, so the two surfaces cannot disagree
	// about what the schedule means.
	await expect(row).toContainText('every day at 19:00');
	await expect(row).toContainText('next in');
});

test('a one-off needs a date as well as a time, and says so', async ({ page }) => {
	await openTasks(page);
	await page.getByTestId('sched-new').click();
	await page.locator('#sched-message').fill('call back');
	await page.locator('#sched-time').fill('19:00');
	await page.getByTestId('sched-save').click();

	await expect(page.getByTestId('schedule-error')).toContainText('date');
	// Caught here, so nothing was sent and no row appeared.
	await expect(page.locator('[data-testid^="sched-row-job-"]')).toHaveCount(0);
});

test('a time that has already gone is refused', async ({ page }) => {
	await openTasks(page);
	await page.getByTestId('sched-new').click();
	await page.locator('#sched-message').fill('too late');
	await page.locator('#sched-date').fill('2020-01-01');
	await page.locator('#sched-time').fill('19:00');
	await page.getByTestId('sched-save').click();
	await expect(page.getByTestId('schedule-error')).toContainText('passed');
});

test('the console can schedule a service call, which the assistant cannot', async ({ page }) => {
	await openTasks(page);
	await page.getByTestId('sched-new').click();
	await page.getByTestId('sched-kind').selectOption('service');
	// And it says the thing that matters about one.
	await expect(page.getByTestId('schedule-editor')).toContainText('approval');

	await page.locator('#sched-service').fill('light.turn_on');
	await page.getByTestId('sched-mode').selectOption('daily');
	await page.locator('#sched-time').fill('18:00');
	await page.getByTestId('sched-save').click();

	const row = rowFor(page, 'light.turn_on');
	await expect(row).toBeVisible({ timeout: 10_000 });
	await expect(row).toHaveAttribute('data-kind', 'service');
});

test('a weekly job needs its days picked', async ({ page }) => {
	await openTasks(page);
	await page.getByTestId('sched-new').click();
	await page.locator('#sched-message').fill('weekly thing');
	await page.getByTestId('sched-mode').selectOption('weekly');
	await page.locator('#sched-time').fill('09:00');
	await page.getByTestId('sched-save').click();
	await expect(page.getByTestId('schedule-error')).toContainText('days');

	await page.getByTestId('sched-day-mon').click();
	await page.getByTestId('sched-day-fri').click();
	await page.getByTestId('sched-save').click();

	await expect(rowFor(page, 'weekly thing')).toContainText('Mon, Fri at 09:00', {
		timeout: 10_000
	});
});

test('a job can be paused and resumed without being forgotten', async ({ page }) => {
	await openTasks(page);
	await page.getByTestId('sched-new').click();
	await page.locator('#sched-message').fill('pausable');
	await page.getByTestId('sched-mode').selectOption('daily');
	await page.locator('#sched-time').fill('20:00');
	await page.getByTestId('sched-save').click();

	const row = rowFor(page, 'pausable');
	await expect(row).toBeVisible({ timeout: 10_000 });
	const id = (await row.getAttribute('data-testid'))!.replace('sched-row-', '');

	await page.getByTestId(`sched-toggle-${id}`).click();
	await expect(row).toHaveAttribute('data-enabled', 'false', { timeout: 10_000 });
	// "off" rather than "done": a paused job and a spent one-shot both have no
	// next firing and are completely different situations.
	await expect(page.getByTestId(`sched-when-${id}`)).toHaveText('off');

	await page.getByTestId(`sched-toggle-${id}`).click();
	await expect(row).toHaveAttribute('data-enabled', 'true', { timeout: 10_000 });
});

test('a job from the config file cannot be removed from here', async ({ page }) => {
	await openTasks(page);
	await expect(page.getByTestId('sched-row-brief')).toBeVisible();
	await expect(page.getByTestId('sched-remove-brief')).toHaveCount(0);
	await expect(page.getByTestId('sched-readonly-brief')).toContainText('configuration.yaml');
});

test('a firing that was missed is surfaced rather than left to be noticed', async ({ page }) => {
	// jarvis-core refuses to replay a backlog, which is right — and a run that
	// quietly did not happen is exactly the thing somebody needs told.
	await openTasks(page);
	await tell(page, {
		type: 'jarvis/test/schedule_missed',
		job_id: 'brief',
		missed: 3,
		reason: 'missed while Jarvis was not running'
	});
	await page.reload();
	await expect(page.getByTestId('sched-note-brief')).toContainText('3 firings missed', {
		timeout: 15_000
	});
	await expect(page.getByTestId('sched-note-brief')).toContainText('not running');
});
