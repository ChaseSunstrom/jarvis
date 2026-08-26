import { test, expect, type Page } from '@playwright/test';

/**
 * Adding somebody else's tools, from the console.
 *
 * Two of these are about a refusal, and refusals are the part of this feature
 * worth an end-to-end test:
 *
 *  * **stdio is not available unless a file on the Jarvis host says so.** A
 *    stdio MCP server runs a program as the jarvis-core user; `allow_stdio`
 *    lives in `configuration.yaml` precisely so that no request — from this
 *    page, from a phone, from a model — can turn it on. The console's job is to
 *    say why the option is closed, not to submit a form the server refuses.
 *  * **a server from the config file is read-only here.** The file is the
 *    operator's statement, and a web request does not get to rewrite it.
 */

async function tell(page: Page, frame: Record<string, unknown>): Promise<void> {
	await page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () => ws.send(JSON.stringify({ id: 77, ...payload }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		frame
	);
}

/**
 * Open the MCP fold. The tools page keeps its rarer sections behind a
 * disclosure (M50) — MCP servers among them — so the panel exists on load and
 * is shown on a click; a reload closes it again.
 */
async function openMcp(page: Page): Promise<void> {
	const fold = page.getByTestId('tools-section-mcp');
	await expect(fold).toBeAttached({ timeout: 15_000 });
	if (!(await fold.getAttribute('open'))) await fold.locator('summary').click();
	await expect(page.getByTestId('mcp-panel')).toBeVisible({ timeout: 15_000 });
}

async function openTools(page: Page): Promise<void> {
	await page.goto('/tools');
	await openMcp(page);
}

test('an http server can be added, and its tools are namespaced', async ({ page }) => {
	await openTools(page);
	await page.getByTestId('mcp-new').click();

	// Typed with a space and a capital: the name is normalised, and the panel
	// has to say so BEFORE saving. Finding out afterwards, from a tool the
	// model calls by a name you did not choose, is the confusing version.
	await page.getByTestId('mcp-name').fill('My Notes');
	await expect(page.getByTestId('mcp-name-normalised')).toContainText('my_notes');
	await expect(page.getByTestId('mcp-preview')).toContainText('mcp_my_notes_');

	await page.getByTestId('mcp-url').fill('http://127.0.0.1:9200/mcp');
	await page.getByTestId('mcp-save').click();

	const row = page.getByTestId('mcp-row-my_notes');
	await expect(row).toBeVisible({ timeout: 10_000 });
	await expect(row).toHaveAttribute('data-connected', 'true');

	// And the tool really is namespaced, which is what stops a server shadowing
	// a built-in.
	await page.getByTestId('mcp-tools-my_notes').click();
	await expect(page.getByTestId('mcp-tool-list-my_notes')).toContainText('mcp_my_notes_search');
});

test('stdio is closed, and the page says where the switch is', async ({ page }) => {
	await openTools(page);
	await page.getByTestId('mcp-new').click();

	const option = page.getByTestId('mcp-transport').locator('option[value="stdio"]');
	await expect(option).toBeDisabled();
	// Visible rather than absent: an option that is simply missing reads as a
	// feature nobody built, and the fix is a line in a file nobody would guess.
	await expect(page.getByTestId('mcp-stdio-note')).toContainText('allow_stdio');
	await expect(page.getByTestId('mcp-stdio-note')).toContainText('configuration.yaml');
});

test('stdio opens once the operator has said so in the file', async ({ page }) => {
	await openTools(page);
	await tell(page, { type: 'jarvis/test/mcp_allow_stdio', allow: true });
	await page.reload();
	await openMcp(page);

	await page.getByTestId('mcp-new').click();
	await expect(
		page.getByTestId('mcp-transport').locator('option[value="stdio"]')
	).toBeEnabled();
	await expect(page.getByTestId('mcp-stdio-note')).toHaveCount(0);

	await page.getByTestId('mcp-name').fill('files');
	await page.getByTestId('mcp-transport').selectOption('stdio');
	await page.getByTestId('mcp-command').fill('npx');
	await page.getByTestId('mcp-args').fill('-y\n@modelcontextprotocol/server-filesystem');
	await page.getByTestId('mcp-tier').selectOption('3');
	await page.getByTestId('mcp-save').click();

	await expect(page.getByTestId('mcp-row-files')).toBeVisible({ timeout: 10_000 });
	await expect(page.getByTestId('mcp-row-files')).toContainText('ASKS');

	// Put it back, so the ordering of this file cannot make another test pass
	// for the wrong reason.
	await tell(page, { type: 'jarvis/test/mcp_allow_stdio', allow: false });
});

test('a server from the config file cannot be removed from here', async ({ page }) => {
	await openTools(page);
	const row = page.getByTestId('mcp-row-house');
	await expect(row).toBeVisible();
	await expect(page.getByTestId('mcp-remove-house')).toHaveCount(0);
	await expect(page.getByTestId('mcp-readonly-house')).toContainText('configuration.yaml');
});

test('a console-added server can be removed, and its tools go with it', async ({ page }) => {
	await openTools(page);
	await page.getByTestId('mcp-new').click();
	await page.getByTestId('mcp-name').fill('scratch');
	await page.getByTestId('mcp-url').fill('http://127.0.0.1:9300/mcp');
	await page.getByTestId('mcp-save').click();
	await expect(page.getByTestId('mcp-row-scratch')).toBeVisible({ timeout: 10_000 });

	await page.getByTestId('mcp-remove-scratch').click();
	await expect(page.getByTestId('mcp-row-scratch')).toHaveCount(0, { timeout: 10_000 });
});

test('a server that has fallen over says why, and can be brought back', async ({ page }) => {
	await openTools(page);
	await tell(page, { type: 'jarvis/test/mcp_break', name: 'house', error: 'no route to host' });
	await page.reload();
	await openMcp(page);

	const row = page.getByTestId('mcp-row-house');
	await expect(row).toHaveAttribute('data-connected', 'false', { timeout: 15_000 });
	await expect(page.getByTestId('mcp-detail-house')).toContainText('no route to host');

	await page.getByTestId('mcp-reconnect-house').click();
	await expect(row).toHaveAttribute('data-connected', 'true', { timeout: 10_000 });
});

test('a url that is not one is caught before it is sent', async ({ page }) => {
	await openTools(page);
	await page.getByTestId('mcp-new').click();
	await page.getByTestId('mcp-name').fill('typo');
	await page.getByTestId('mcp-url').fill('notes.local/mcp');
	await page.getByTestId('mcp-save').click();

	await expect(page.getByTestId('mcp-error')).toContainText('http');
	await expect(page.getByTestId('mcp-row-typo')).toHaveCount(0);
});

test('inspect shows the schemas and, when it is down, why', async ({ page }) => {
	// The failure this covers: a server that is simply missing from the tool
	// list tells nobody why. `last_error` is what the view exists for, and the
	// schema below it is the answer to nine failing tool calls in ten.
	//
	// It knocks the server over itself rather than relying on the state an
	// earlier test left: the tests in this file share one mock, and this one
	// asserted on a tool list a previous test had emptied — which is a test
	// depending on its neighbours, not a defect in the panel.
	await openTools(page);
	await tell(page, { type: 'jarvis/test/mcp_break', name: 'house', error: 'no route to host' });
	await page.reload();
	await openMcp(page);

	await page.getByTestId('mcp-tools-house').click();
	const detail = page.getByTestId('mcp-inspect-house');
	await expect(detail).toBeVisible();
	await expect(page.getByTestId('mcp-protocol-house')).toContainText('2025-06-18');
	await expect(page.getByTestId('mcp-last-error-house')).toContainText('connection refused');

	// Back up, and now the tools — with their arguments in full.
	await page.getByTestId('mcp-reconnect-house').click();
	await expect(page.getByTestId('mcp-row-house')).toHaveAttribute('data-connected', 'true', {
		timeout: 10_000
	});
	const schema = page.getByTestId('mcp-schema-mcp_house_read_note');
	await expect(schema).toContainText('"id"');
	await expect(schema).toContainText('required');

	// And a test call, which goes through the SAME gate the model does — a
	// console-only execution path would be a way around the approval gate, and
	// the whole argument for the gate is that there is only one.
	await page.getByTestId('mcp-try-mcp_house_read_note').click();
	await expect(page.getByTestId('mcp-result-mcp_house_read_note')).toBeVisible({
		timeout: 10_000
	});
});
