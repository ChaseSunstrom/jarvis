import { test, expect, type Page } from '@playwright/test';

/**
 * Jarvis Code, from the console.
 *
 * Three claims, and each fails in a way that is invisible without a test:
 *
 * 1. **A job started here reports through the same task machinery.** The bar on
 *    this page is the bar on `/tasks` and the bar on the phone, because it is
 *    the same record. A Code page with a progress display of its own would look
 *    identical and be a second thing to keep true.
 * 2. **The plan the model wrote becomes the steps.** A job that starts with one
 *    step and grows to five is the honest shape — indeterminate until the plan
 *    lands, a real fraction afterwards — and a console that showed a fraction
 *    the whole way would be inventing a denominator.
 * 3. **Read-only says so before you type, not after.** Somebody who writes an
 *    instruction into a repository Jarvis may not change should know from the
 *    line under the picker.
 */

async function tell(page: Page, frame: Record<string, unknown>): Promise<void> {
	await page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () => ws.send(JSON.stringify({ id: 93, ...payload }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		frame
	);
}

const reset = (page: Page, sandboxed = false) =>
	tell(page, { type: 'jarvis/test/code_reset', sandboxed });

async function openCode(page: Page): Promise<void> {
	await page.goto('/code');
	await expect(page.getByTestId('code-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
}

test('a job runs, its bar moves, and its diff is there at the end', async ({ page }) => {
	await openCode(page);
	await reset(page);

	await page.getByTestId('code-repo').selectOption('jarvis');
	await page.getByTestId('code-instruction').fill('make handle() return 2 instead of 1');
	await page.getByTestId('code-start').click();

	const card = page.locator('[data-testid^="task-card-"][data-kind="code"]').first();
	await expect(card).toBeVisible({ timeout: 10_000 });

	// The plan lands and the bar stops being indeterminate: one step becomes
	// five. This is the assertion that fails if the plan never reaches the
	// registry, which is the whole reason the planning call exists.
	await expect
		.poll(async () => (await card.innerText()).includes('5 steps'), { timeout: 15_000 })
		.toBe(true);

	// START opens the job it just began — you pressed the button to watch this
	// one — so the diff arrives without a second click.
	const id = (await card.getAttribute('data-testid'))!.replace('task-card-', '');
	await expect(page.getByTestId(`code-detail-${id}`)).toContainText('Still running');

	await expect(card).toHaveAttribute('data-status', 'done', { timeout: 20_000 });
	await expect(page.getByTestId('code-branch')).toContainText('jarvis/', { timeout: 10_000 });
	await expect(page.getByTestId('code-diff')).toContainText('return 2');
	await expect(page.getByTestId('code-checks')).toContainText('1/2 checks passed');

	// And the toggle shuts it again, because a page of five finished jobs
	// should not be five diffs.
	await page.getByTestId(`code-open-${id}`).click();
	await expect(page.getByTestId(`code-detail-${id}`)).toHaveCount(0);
});

test('the same job is on the tasks page, because it is the same record', async ({ page }) => {
	await openCode(page);
	await reset(page);
	await page.getByTestId('code-repo').selectOption('jarvis');
	await page.getByTestId('code-instruction').fill('add a null check to the handler');
	await page.getByTestId('code-start').click();
	await expect(page.locator('[data-testid^="task-card-"][data-kind="code"]').first()).toBeVisible({
		timeout: 10_000
	});

	await page.goto('/tasks');
	await expect(page.getByTestId('tasks-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
	await expect(page.locator('[data-testid^="task-card-"][data-kind="code"]').first()).toContainText(
		'add a null check',
		{ timeout: 10_000 }
	);
});

test('a read-only repository says so before anything is typed', async ({ page }) => {
	await openCode(page);
	await reset(page);
	await page.getByTestId('code-repo').selectOption('notes');
	await expect(page.getByTestId('code-repo-note')).toContainText('read-only');
	// And a repository with no checks says that too, rather than leaving the
	// reader to guess that `run_check` will do nothing.
	await expect(page.getByTestId('code-repo-note')).toContainText('no checks');
});

test('START is refused until there is something to act on', async ({ page }) => {
	await openCode(page);
	await reset(page);
	await expect(page.getByTestId('code-start')).toBeDisabled();
	await page.getByTestId('code-instruction').fill('fix it');
	// Not arbitrary: the job cannot ask a follow-up question.
	await expect(page.getByTestId('code-blocked')).toContainText('bit more');
	await expect(page.getByTestId('code-start')).toBeDisabled();
	await page.getByTestId('code-instruction').fill('make handle() return 2');
	await expect(page.getByTestId('code-start')).toBeEnabled();
});

test('the sandbox line does not claim more than it can', async ({ page }) => {
	await openCode(page);
	await reset(page, false);
	await page.reload();
	await expect(page.getByTestId('code-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
	// With no wrapper the model still has no shell and cannot leave the
	// repository, so the sentence names the one thing that actually changes.
	await expect(page.getByTestId('code-sandbox')).toContainText('Checks run as the server does');

	await reset(page, true);
	await page.reload();
	await expect(page.getByTestId('code-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
	await expect(page.getByTestId('code-sandbox')).toContainText('wrapper');
});

test('CODE is in the nav and reachable by its chord', async ({ page }) => {
	await page.goto('/tasks');
	await expect(page.getByTestId('tasks-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
	await page.keyboard.press('g');
	await page.keyboard.press('c');
	await expect(page).toHaveURL(/\/code$/, { timeout: 10_000 });
});
