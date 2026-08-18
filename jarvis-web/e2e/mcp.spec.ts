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

async function openTools(page: Page): Promise<void> {
	await page.goto('/tools');
	await expect(page.getByTestId('mcp-panel')).toBeVisible({ timeout: 15_000 });
}

test('an http server can be added, and its tools are namespaced', async ({ page }) => {
	await openTools(page);
	await page.getByTestId('mcp-new').click();

	// Typed with a space and a capital: the name is normalised, and the panel
	// has to say so BEFORE saving. Finding out afterwards, from a tool the
	// model calls by a name you did not choose, is the confusing version.
	await page.locator('#mcp-name').fill('My Notes');
	await expect(page.getByTestId('mcp-name-normalised')).toContainText('my_notes');
	await expect(page.getByTestId('mcp-preview')).toContainText('mcp_my_notes_');

	await page.locator('#mcp-url').fill('http://127.0.0.1:9200/mcp');
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
	await expect(page.getByTestId('mcp-panel')).toBeVisible({ timeout: 15_000 });

	await page.getByTestId('mcp-new').click();
	await expect(
		page.getByTestId('mcp-transport').locator('option[value="stdio"]')
	).toBeEnabled();
	await expect(page.getByTestId('mcp-stdio-note')).toHaveCount(0);

	await page.locator('#mcp-name').fill('files');
	await page.getByTestId('mcp-transport').selectOption('stdio');
	await page.locator('#mcp-command').fill('npx');
	await page.locator('#mcp-args').fill('-y\n@modelcontextprotocol/server-filesystem');
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
	await page.locator('#mcp-name').fill('scratch');
	await page.locator('#mcp-url').fill('http://127.0.0.1:9300/mcp');
	await page.getByTestId('mcp-save').click();
	await expect(page.getByTestId('mcp-row-scratch')).toBeVisible({ timeout: 10_000 });

	await page.getByTestId('mcp-remove-scratch').click();
	await expect(page.getByTestId('mcp-row-scratch')).toHaveCount(0, { timeout: 10_000 });
});

test('a server that has fallen over says why, and can be brought back', async ({ page }) => {
	await openTools(page);
	await tell(page, { type: 'jarvis/test/mcp_break', name: 'house', error: 'no route to host' });
	await page.reload();

	const row = page.getByTestId('mcp-row-house');
	await expect(row).toHaveAttribute('data-connected', 'false', { timeout: 15_000 });
	await expect(page.getByTestId('mcp-detail-house')).toContainText('no route to host');

	await page.getByTestId('mcp-reconnect-house').click();
	await expect(row).toHaveAttribute('data-connected', 'true', { timeout: 10_000 });
});

test('a url that is not one is caught before it is sent', async ({ page }) => {
	await openTools(page);
	await page.getByTestId('mcp-new').click();
	await page.locator('#mcp-name').fill('typo');
	await page.locator('#mcp-url').fill('notes.local/mcp');
	await page.getByTestId('mcp-save').click();

	await expect(page.getByTestId('mcp-error')).toContainText('http');
	await expect(page.getByTestId('mcp-row-typo')).toHaveCount(0);
});
