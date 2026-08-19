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
