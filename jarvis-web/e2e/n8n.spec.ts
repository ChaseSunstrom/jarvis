import { test, expect, type Page } from '@playwright/test';

/**
 * The n8n page.
 *
 * ## What is worth an end-to-end test here
 *
 * Not "the list renders". The thing that would be quietly useless is the
 * CONNECTIONS line: Jarvis strips the credential id the model guessed, so a
 * workflow it wrote cannot run, and the only place that fact becomes
 * actionable is next to the workflow with the node named. If that line does
 * not appear, the feature is a workflow nobody can use and no explanation.
 *
 * The other two are the setup path — configured is not the same as working,
 * so CHECK has to say which — and that node PARAMETERS never reach the page.
 */

const OPEN = { timeout: 15_000 };

async function tell(page: Page, frame: Record<string, unknown>): Promise<void> {
	await page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () => ws.send(JSON.stringify({ id: 88, ...payload }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		frame
	);
}

async function openN8n(page: Page): Promise<void> {
	await page.goto('/n8n');
	await expect(page.getByTestId('n8n-lede')).toHaveAttribute('data-redialling', 'false', OPEN);
}

test.beforeEach(async ({ page }) => {
	await page.goto('/n8n');
	await tell(page, { type: 'jarvis/test/n8n_reset' });
});

test('a workflow says what has to be connected before it can run', async ({ page }) => {
	// The point of the page. Jarvis strips the guessed credential id, so this
	// line is the only thing standing between "created" and "why is it broken".
	await openN8n(page);
	await page.getByTestId('n8n-open-wf-receipts').click();

	const needed = page.getByTestId('n8n-connections-needed');
	await expect(needed).toBeVisible(OPEN);
	await expect(needed).toContainText('gmailOAuth2');
	await expect(needed).toContainText('Gmail');
	await expect(needed).toContainText('Credentials');
});

test('a workflow with everything attached does not nag', async ({ page }) => {
	await openN8n(page);
	await page.getByTestId('n8n-open-wf-nightly').click();
	await expect(page.getByTestId('n8n-node-S3')).toBeVisible(OPEN);
	await expect(page.getByTestId('n8n-connections-needed')).toHaveCount(0);
});

test('node parameters never reach the page', async ({ page }) => {
	// jarvis-core does not send them — people type API keys into an HTTP
	// node's header fields — and this is the assertion that notices if that
	// ever changes.
	await openN8n(page);
	await page.getByTestId('n8n-open-wf-nightly').click();
	const detail = page.getByTestId('n8n-detail-wf-nightly');
	await expect(detail).toBeVisible(OPEN);
	await expect(detail).toContainText('not shown here');
});

test('a workflow can be switched on and off from the console', async ({ page }) => {
	// `allow_activate` is off in the fixture, and this still works: that flag
	// is about what JARVIS does on its own, and a person pressing a button is
	// the human it exists to insist on.
	await openN8n(page);
	await expect(page.getByTestId('n8n-activate-note')).toContainText('may not switch');

	const state = page.getByTestId('n8n-state-wf-receipts');
	await expect(state).toHaveAttribute('data-active', 'false');
	await page.getByTestId('n8n-toggle-wf-receipts').click();
	await expect(state).toHaveAttribute('data-active', 'true', OPEN);
	await expect(page.getByTestId('toast').first()).toContainText('live');
});

test('CHECK says whether it actually works, not whether it is configured', async ({ page }) => {
	await openN8n(page);
	await page.getByTestId('n8n-check').click();
	const result = page.getByTestId('n8n-check-result');
	await expect(result).toHaveAttribute('data-ok', 'true', OPEN);
	await expect(result).toContainText('Connected');
});

test('a bad key is reported as a sentence, not an empty list', async ({ page }) => {
	await page.goto('/n8n');
	await tell(page, {
		type: 'jarvis/test/n8n_reset',
		check: { ok: false, detail: 'n8n refused the API key (401). Settings -> n8n API.' }
	});
	await openN8n(page);
	await page.getByTestId('n8n-check').click();
	const result = page.getByTestId('n8n-check-result');
	await expect(result).toHaveAttribute('data-ok', 'false', OPEN);
	await expect(result).toContainText('401');
});

test('an unconfigured server shows the two lines to add', async ({ page }) => {
	await page.goto('/n8n');
	await tell(page, {
		type: 'jarvis/test/n8n_reset',
		instance: { url: '', has_key: false, configured: false }
	});
	await openN8n(page);

	await expect(page.getByTestId('n8n-instance-note')).toContainText('configuration.yaml');
	await expect(page.getByTestId('n8n-snippet')).toContainText('N8N_API_KEY');
	// Nothing to list, and no misleading empty workflow panel either.
	await expect(page.getByTestId('n8n-workflows')).toHaveCount(0);

	await tell(page, { type: 'jarvis/test/n8n_reset' });
});

test('N8N is in the nav and reachable by its chord', async ({ page }) => {
	await page.goto('/tasks');
	await expect(page.getByTestId('tasks-lede')).toHaveAttribute('data-redialling', 'false', OPEN);
	// The boot animation swallows the first key — see e2e/code.spec.ts.
	await expect(page.getByTestId('boot')).toHaveCount(0, OPEN);
	await page.keyboard.press('g');
	await page.keyboard.press('n');
	await expect(page).toHaveURL(/\/n8n$/, { timeout: 10_000 });
});

// ---------------------------------------------------------------------------
// the three-layer CHECK, and n8n's own AI builder
// ---------------------------------------------------------------------------
test('CHECK says which of the three layers is missing, not just that it failed', async ({
	page
}) => {
	// The state most self-hosted users are actually in: API key fine, login
	// fine, and the AI builder gated behind a licence they do not have. A
	// single "n8n: no" would send them to check the key.
	await openN8n(page);
	await page.getByTestId('n8n-check').click();

	const layers = page.getByTestId('n8n-layers');
	await expect(layers).toBeVisible(OPEN);
	await expect(page.getByTestId('n8n-layer-public-api')).toHaveAttribute(
		'data-available',
		'true'
	);
	await expect(page.getByTestId('n8n-layer-login')).toHaveAttribute('data-available', 'true');
	const builder = page.getByTestId('n8n-layer-ai-builder');
	await expect(builder).toHaveAttribute('data-available', 'false');
	await expect(builder).toContainText('two separate switches');
});

test('the builder box is not offered when the licence does not include it', async ({ page }) => {
	// A form that submits into a 403 is worse than no form — and the reason is
	// shown either way, because somebody who wired up a model deserves to know
	// which of the two switches they are missing.
	await openN8n(page);
	await expect(page.getByTestId('n8n-builder')).toHaveCount(0);

	await page.getByTestId('n8n-check').click();
	await expect(page.getByTestId('n8n-builder')).toBeVisible(OPEN);
	await expect(page.getByTestId('n8n-build')).toHaveCount(0);
	await expect(page.getByTestId('n8n-builder-problem')).toContainText('licence');
});

test('a licensed instance can hand a request to n8n’s builder', async ({ page }) => {
	await page.goto('/n8n');
	await tell(page, {
		type: 'jarvis/test/n8n_reset',
		check: {
			ok: true,
			detail: 'Connected to http://n8n.lan:5678.',
			capabilities: {
				api: { available: true, reason: '', detail: 'Connected.' },
				login: { available: true, reason: '', detail: 'Logged in.' },
				builder: { available: true, reason: '', detail: 'n8n says its AI builder is licensed.' },
				checked_at: 1
			}
		}
	});
	await openN8n(page);
	await page.getByTestId('n8n-check').click();

	await page.getByTestId('n8n-instruction').fill('Every morning email me the orders');
	await page.getByTestId('n8n-build').click();

	// It returns at once, because the builder can stop to ask a question and a
	// question cannot be answered inside the request that raised it.
	const started = page.getByTestId('n8n-build-started');
	await expect(started).toBeVisible(OPEN);
	await expect(started).toContainText('Tasks page');

	await tell(page, { type: 'jarvis/test/n8n_reset' });
});

test('the builder transcript is marked as somebody else’s words', async ({ page }) => {
	await page.goto('/n8n');
	await tell(page, {
		type: 'jarvis/test/n8n_reset',
		check: {
			ok: true,
			detail: 'Connected.',
			capabilities: {
				api: { available: true, reason: '', detail: 'Connected.' },
				login: { available: true, reason: '', detail: 'Logged in.' },
				builder: { available: true, reason: '', detail: 'licensed' },
				checked_at: 1
			}
		}
	});
	await openN8n(page);
	await page.getByTestId('n8n-check').click();
	await page.getByTestId('n8n-instruction').fill('do a thing');
	await page.getByTestId('n8n-build').click();
	await expect(page.getByTestId('n8n-build-started')).toBeVisible(OPEN);

	await page.getByTestId('n8n-transcript').click();
	const lines = page.getByTestId('n8n-transcript-lines');
	await expect(lines).toBeVisible(OPEN);
	// Prose from a different AI, labelled as such rather than reading as
	// Jarvis's own voice.
	await expect(lines).toContainText("n8n's builder");
	await expect(lines).toContainText('Which mailbox');

	await tell(page, { type: 'jarvis/test/n8n_reset' });
});

test('"is it working?" answers the question the workflow list cannot', async ({ page }) => {
	// Connected, switched on, and never run looks identical to working from
	// every other angle — and it is what a schedule in the wrong timezone
	// does. This is the only place the three reads are joined.
	await openN8n(page);
	await page.getByTestId('n8n-open-wf-receipts').click();
	await expect(page.getByTestId('n8n-detail-wf-receipts')).toBeVisible(OPEN);

	await page.getByTestId('n8n-health-wf-receipts').click();
	const result = page.getByTestId('n8n-health-result');
	await expect(result).toBeVisible(OPEN);
	await expect(result).toContainText('Not connected yet');
	await expect(result).toContainText('Credentials -> New');
});
