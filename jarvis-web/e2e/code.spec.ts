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
	// repository, so the sentence names the one thing that actually changes —
	// where the checks run, which since the host-check refusal may be nowhere.
	await expect(page.getByTestId('code-sandbox')).toContainText('environment');
	await expect(page.getByTestId('code-sandbox')).not.toContainText('unsafe');

	await reset(page, true);
	await page.reload();
	await expect(page.getByTestId('code-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
	await expect(page.getByTestId('code-sandbox')).toContainText('wrapper');
});

test('CODE is a section of WORK, and its chord still finds it', async ({ page }) => {
	await page.goto('/tasks');
	await expect(page.getByTestId('tasks-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});
	// The boot animation swallows the FIRST key — `BootSequence` registers a
	// one-shot `keydown` that jumps the timeline to its end, so a `g` pressed
	// while it plays skips the animation instead of arming the chord, and only
	// the `c` reaches the handler. That is deliberate behaviour and invisible
	// on a fast machine, which is exactly why this raced: it failed on the
	// slow runs and passed on the quick ones.
	await expect(page.getByTestId('boot')).toHaveCount(0, { timeout: 15_000 });
	await page.keyboard.press('g');
	await page.keyboard.press('c');
	await expect(page).toHaveURL(/\/work\/code$/, { timeout: 10_000 });
});


test('a repository can be created from the console', async ({ page }) => {
	// The thing that was impossible before: asked for something new, there was
	// nowhere to put it.
	await openCode(page);
	await reset(page);

	await page.getByTestId('code-new-repo').click();
	await page.getByTestId('repo-name').fill('snake-opengl');
	await page.getByTestId('repo-description').fill('a snake game');
	await page.getByTestId('repo-environment').selectOption('python');
	// The environment line says what a job there will be allowed to do, before
	// the button is pressed.
	await expect(page.getByTestId('repo-environment-note')).toContainText('reach the internet');

	await page.getByTestId('repo-create').click();
	await expect(page.getByTestId('code-repo')).toContainText('snake-opengl', {
		timeout: 10_000
	});

	// And it is immediately usable as a job target.
	await page.getByTestId('code-repo').selectOption('snake-opengl');
	await expect(page.getByTestId('code-repo-environment')).toHaveAttribute(
		'data-networked',
		'true'
	);
	await expect(page.getByTestId('code-repo-egress')).toContainText('outbound connections');
});

test('a bad repository name is refused before it is sent', async ({ page }) => {
	await openCode(page);
	await reset(page);
	await page.getByTestId('code-new-repo').click();

	await page.getByTestId('repo-name').fill('../etc');
	await expect(page.getByTestId('repo-name-problem')).toBeVisible();
	await expect(page.getByTestId('repo-create')).toBeDisabled();

	await page.getByTestId('repo-name').fill('Snake');
	await expect(page.getByTestId('repo-name-problem')).toContainText('lowercase');
	await expect(page.getByTestId('repo-create')).toBeDisabled();

	await page.getByTestId('repo-name').fill('node_modules');
	await expect(page.getByTestId('repo-name-problem')).toContainText('reserved');

	await page.getByTestId('repo-name').fill('snake');
	await expect(page.getByTestId('repo-name-problem')).toHaveCount(0);
	await expect(page.getByTestId('repo-create')).toBeEnabled();
});

test('a forge repository can be cloned from the console', async ({ page }) => {
	// The other half of "I should be able to create them in the web UI": most
	// work starts from something that already exists somewhere else.
	await openCode(page);
	await reset(page);

	await page.getByTestId('code-clone-repo').click();
	await expect(page.getByTestId('clone-forge-note')).toContainText('chasesunstrom/jarvis');

	await page.getByTestId('clone-project').fill('chasesunstrom/widgets');
	await page.getByTestId('clone-environment').selectOption('python');
	// The local name defaults to the last segment, the way git does.
	await expect(page.getByTestId('clone-name')).toHaveAttribute('placeholder', 'widgets');

	await page.getByTestId('clone-start').click();
	await expect(page.getByTestId('code-repo')).toContainText('widgets', { timeout: 10_000 });

	await page.getByTestId('code-repo').selectOption('widgets');
	await expect(page.getByTestId('code-repo-environment')).toHaveAttribute(
		'data-networked',
		'true'
	);
});

test('a repository outside the allow-list is refused before it is sent', async ({ page }) => {
	// The allow-list is the answer to "give access to only certain
	// repositories". jarvis-core enforces it; this says so without the wait.
	await openCode(page);
	await reset(page);
	await page.getByTestId('code-clone-repo').click();

	await page.getByTestId('clone-project').fill('someone-else/private');
	await expect(page.getByTestId('clone-project-problem')).toContainText('allow-list');
	await expect(page.getByTestId('clone-start')).toBeDisabled();

	// A bare name does not say whose, and neither forge would match it.
	await page.getByTestId('clone-project').fill('jarvis');
	await expect(page.getByTestId('clone-project-problem')).toContainText('owner/repo');

	// `chasesunstrom/*` covers a whole owner.
	await page.getByTestId('clone-project').fill('chasesunstrom/anything');
	await expect(page.getByTestId('clone-project-problem')).toHaveCount(0);
	await expect(page.getByTestId('clone-start')).toBeEnabled();
});

test('a read-only forge and a missing token both say so', async ({ page }) => {
	await openCode(page);
	await reset(page);
	await page.getByTestId('code-clone-repo').click();

	await page.getByTestId('clone-forge').selectOption('mirror');
	await expect(page.getByTestId('clone-forge-note')).toContainText('cannot push branches back');
	await expect(page.getByTestId('clone-forge-token')).toContainText('No token configured');
});

test('an empty console says how to end up with a repository', async ({ page }) => {
	// What the operator actually saw: an empty picker over "Pick a repository
	// first", which names what is missing and not how to fix it.
	await openCode(page);
	await tell(page, { type: 'jarvis/test/code_empty' });
	await page.reload();
	await expect(page.getByTestId('code-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});

	await expect(page.getByTestId('code-repo')).toBeDisabled();
	const note = page.getByTestId('code-no-repos');
	await expect(note).toContainText('NEW REPOSITORY');
	await expect(note).toContainText('CLONE FROM A FORGE');

	// Put the declared repositories back. One mock process serves the file and
	// the tests below load their page BEFORE they reset, so a test that empties
	// the list and walks away decides what the next one sees.
	await reset(page);
});

test('a repository with no environment says it has no shell', async ({ page }) => {
	await openCode(page);
	await reset(page);
	await page.getByTestId('code-repo').selectOption('notes');
	await expect(page.getByTestId('code-repo-environment')).toContainText('No shell');
	await expect(page.getByTestId('code-repo-egress')).toHaveCount(0);
});
