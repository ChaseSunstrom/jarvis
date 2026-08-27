import { expect, test, type Page } from '@playwright/test';

/**
 * M65 — something to browse.
 *
 * The operator's report, verbatim: "I cant browse the tools/mcp servers from
 * the settings, no way to browse". Two things were true: the browse button
 * was inside a fold, and what it opened was empty, because no catalogue
 * source is configured by default. The claims worth a browser here:
 *
 *   - the catalogue is on the tools page at rest, above the folds, with the
 *     shipped skills in it saying INSTALLED — no fold has to be opened;
 *   - the page's ONE search filters it (the inventory allows one box);
 *   - an entry installs through the same two-step flow M47 built — the plan
 *     with the ref, the hash, the permissions and every program, then the
 *     approval — and the row says INSTALLED afterwards;
 *   - MCP is add-by-URL, one press away from the catalogue's MCP line;
 *   - a source that cannot be read shows its reason; no source at all says
 *     how one gets there.
 *
 * What the browser cannot prove: that the shipped index agrees with the
 * SKILL.md files beside it, and that browse answers from a real jarvis-core
 * with no configuration — `jarvis-core/tests/test_extensions.py` does both.
 */

/** A word to the mock over its own socket (the same hook the other specs use). */
async function tell(page: Page, frame: Record<string, unknown>): Promise<void> {
	await page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
				ws.onopen = () => ws.send(JSON.stringify({ id: 65, ...payload }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		frame
	);
}

const SHIPPED = ['diary', 'homelab-status', 'note-taking', 'research-report'];

async function open(page: Page): Promise<void> {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto('/settings/tools');
	await expect(page.getByTestId('catalogue-section')).toBeVisible({ timeout: 15_000 });
}

// The mock is one process for the whole run and an install pushes a row into
// it, so every test here starts from the shipped state — otherwise a spec
// that installed `bin-day` earlier leaves this one looking at INSTALLED.
test.beforeEach(async ({ page }) => {
	await page.goto('/healthz');
	await tell(page, { type: 'jarvis/test/extensions_reset' });
});

test('the catalogue is the first thing on the tools page, with no fold to open', async ({ page }) => {
	await open(page);

	// The shipped skills, each saying INSTALLED, drawn without a click.
	for (const id of SHIPPED) {
		await expect(page.getByTestId(`catalog-${id}`)).toBeVisible();
		await expect(page.getByTestId(`catalog-installed-${id}`)).toHaveText('INSTALLED');
		await expect(page.getByTestId(`catalog-install-${id}`)).toHaveCount(0);
	}
	// And the fixture's, with one INSTALL each and what it asks for on the row.
	await expect(page.getByTestId('catalog-install-bin-day')).toBeVisible();
	await expect(page.getByTestId('catalog-perms-friendly-helper')).toContainText('run_process');
	// 6 shipped/fixture entries + the four the registries list in the mock since M108.
	await expect(page.getByTestId('catalogue-meta')).toHaveText('10 available · 4 installed');

	// Above every fold — not inside one, and not below the first.
	const catalogue = await page.getByTestId('catalogue-section').boundingBox();
	const firstFold = await page.locator('main details.fold').first().boundingBox();
	expect(catalogue && firstFold && catalogue.y + catalogue.height <= firstFold.y + 1).toBe(true);
	expect(await page.locator('details.fold [data-testid="catalogue-section"]').count()).toBe(0);
	// The old door is gone: one way to the catalogue, not two.
	await expect(page.getByTestId('extensions-browse')).toHaveCount(0);

	// The hostile description arrives wrapped, and it is shown as text rather
	// than obeyed — the marker is the visible evidence that it is data.
	await expect(page.getByTestId('catalog-friendly-helper')).toContainText('untrusted_content');
});

test("the page's one search filters the catalogue as it filters the folds", async ({ page }) => {
	await open(page);
	const search = page.locator('main [data-jv-filter]');
	await expect(search).toHaveCount(1);

	await search.fill('bin');
	await expect(page.getByTestId('catalog-bin-day')).toBeVisible();
	await expect(page.getByTestId('catalog-diary')).toHaveCount(0);
	await expect(page.getByTestId('catalogue-meta')).toHaveText('1 of 10 match');

	// A permission is a word too: "network" finds the skill that asks for it.
	await search.fill('network');
	await expect(page.getByTestId('catalog-research-report')).toBeVisible();
	await expect(page.getByTestId('catalog-diary')).toHaveCount(0);

	await search.fill('zzzz-nothing-is-called-this');
	await expect(page.getByTestId('catalogue-no-match')).toBeVisible();
	await expect(page.locator('[data-testid^="catalog-"][data-jv-row]')).toHaveCount(0);

	await search.fill('');
	await expect(page.locator('[data-testid^="catalog-"][data-jv-row]')).toHaveCount(10);
});

test('installing is a second decision, with the hash and every program named, and the row follows', async ({ page }) => {
	await open(page);
	await page.getByTestId('catalog-install-friendly-helper').click();

	const plan = page.getByTestId('install-plan');
	await expect(plan).toBeVisible();
	// Pinned, hashed, and the permissions are the ones the entry declared.
	await expect(plan).toContainText('v2.1.0');
	await expect(plan).toContainText('a'.repeat(16));
	await expect(page.getByTestId('install-permissions')).toContainText('run_process');
	// The program in the payload is named before the button is pressed.
	await expect(page.getByTestId('install-hooks')).toContainText('install.sh');
	await expect(page.getByTestId('install-hooks')).toContainText('will not run');

	await page.getByTestId('install-confirm').click();
	// The catalogue row says so, and the Extensions fold (open at rest)
	// re-read without a reload.
	await expect(page.getByTestId('catalog-installed-friendly-helper')).toHaveText('INSTALLED');
	await expect(page.getByTestId('catalog-install-friendly-helper')).toHaveCount(0);
	await expect(page.getByTestId('ext-skill:friendly-helper')).toBeVisible();
	await expect(page.getByTestId('catalogue-meta')).toHaveText('10 available · 5 installed');
});

test('a benign entry installs with no program to warn about', async ({ page }) => {
	await open(page);
	await page.getByTestId('catalog-install-bin-day').click();
	await expect(page.getByTestId('install-plan')).toBeVisible();
	await expect(page.getByTestId('install-hooks')).toHaveCount(0);
	await page.getByTestId('install-confirm').click();
	await expect(page.getByTestId('catalog-installed-bin-day')).toBeVisible();
	await expect(page.getByTestId('ext-skill:bin-day')).toBeVisible();
});

test('MCP is add-by-URL, one press from the catalogue, and says why stdio is not here', async ({ page }) => {
	await open(page);
	const line = page.getByTestId('catalogue-mcp');
	await expect(line).toContainText('added by URL in the MCP servers fold below');
	await expect(line).toContainText('allow_stdio');

	// The fold is closed at rest; the line's one control opens it on its form.
	await expect(page.getByTestId('mcp-editor')).toHaveCount(0);
	await page.getByTestId('catalogue-add-mcp').click();
	await expect(page.getByTestId('mcp-editor')).toBeVisible();
	await expect(page.getByTestId('mcp-url')).toBeVisible();
	await expect(page.getByTestId('mcp-stdio-note')).toContainText('allow_stdio');
	await expect(page.getByTestId('mcp-name')).toBeFocused();
});

test('a source that cannot be read shows its reason, and Retry reads it again', async ({ page }) => {
	await page.goto('/healthz');
	await tell(page, { type: 'jarvis/test/catalog_mode', mode: 'broken' });
	try {
		await open(page);
		const error = page.getByTestId('catalogue-error');
		await expect(error).toBeVisible();
		await expect(error).toContainText('no catalog index');
		await expect(error).toContainText('bundled');
		await expect(page.locator('[data-testid^="catalog-"][data-jv-row]')).toHaveCount(0);
		// The MCP line does not depend on the catalogue being readable.
		await expect(page.getByTestId('catalogue-mcp')).toBeVisible();

		await tell(page, { type: 'jarvis/test/catalog_mode', mode: 'ok' });
		await error.getByTestId('retry').click();
		await expect(page.getByTestId('catalog-diary')).toBeVisible();
	} finally {
		await tell(page, { type: 'jarvis/test/catalog_mode', mode: 'ok' });
	}
});

test('no source at all says what would be here and how it gets there', async ({ page }) => {
	await page.goto('/healthz');
	await tell(page, { type: 'jarvis/test/catalog_mode', mode: 'none' });
	try {
		await open(page);
		const empty = page.getByTestId('catalogue-empty');
		await expect(empty).toBeVisible();
		await expect(empty).toContainText('configuration.yaml');
		await expect(page.getByTestId('catalogue-error')).toHaveCount(0);
	} finally {
		await tell(page, { type: 'jarvis/test/catalog_mode', mode: 'ok' });
	}
});
