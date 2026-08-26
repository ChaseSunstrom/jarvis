import { expect, test } from '@playwright/test';

/**
 * M46 — the management surface.
 *
 * The claims worth a browser: a row per installed thing whatever kind it is,
 * one switch that reaches the server, a permission scope that can be narrowed
 * from the page, a rejected manifest that says why rather than being absent,
 * and a skill that can be written without anybody opening a file.
 *
 * What the browser CANNOT prove is that turning something off takes its tools
 * off the model — that is `tests/test_extensions.py` and the live suite,
 * against the real registry.
 */

const open = async (page: import('@playwright/test').Page) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto('/tools');
	await expect(page.getByTestId('extensions-panel')).toBeVisible({ timeout: 15_000 });
};

test('every kind of installed thing is one list, with its state on the row', async ({ page }) => {
	await open(page);

	// One list: a skill, a plugin and an MCP server, not three panels.
	await expect(page.getByTestId('ext-skill:research-report')).toBeVisible();
	await expect(page.getByTestId('ext-plugin:calendar')).toBeVisible();
	await expect(page.getByTestId('ext-mcp:notes-server')).toBeVisible();

	// A server that is not working says so on the row, not two clicks in.
	await expect(page.getByTestId('ext-sick-mcp:notes-server')).toBeVisible();

	// Never-used and recently-used are different sentences.
	const shipped = page.getByTestId('ext-skill:research-report');
	await expect(shipped).toContainText(/used \d+m ago|used just now/);
	await expect(page.getByTestId('ext-skill:house-style')).toContainText('never used');
});

test('a rejected manifest says why, instead of simply not being there', async ({ page }) => {
	await open(page);
	const rejected = page.getByTestId('ext-rejected-bad-manifest');
	await expect(rejected).toBeVisible();
	await expect(rejected).toContainText('become_root');
});

test('the switch reaches the server, and the row follows', async ({ page }) => {
	await open(page);
	const toggle = page.getByTestId('ext-toggle-plugin:calendar');
	await expect(toggle).toBeChecked();
	await toggle.click();
	await expect(page.getByTestId('ext-toggle-plugin:calendar')).not.toBeChecked();
});

test('the permission scope can be narrowed from the row', async ({ page }) => {
	await open(page);
	await page.getByTestId('ext-open-plugin:calendar').click();
	const detail = page.getByTestId('ext-detail-plugin:calendar');
	await expect(detail).toBeVisible();
	// Progressive disclosure: the tool list is behind the expander, not on the row.
	await expect(detail).toContainText('calendar_create');

	const act = page.getByTestId('ext-perm-plugin:calendar-act');
	await expect(act).toBeChecked();
	await act.click();
	await expect(page.getByTestId('ext-plugin:calendar')).toContainText('NARROWED');
});

test('a skill can be written without anybody opening a file', async ({ page }) => {
	await open(page);
	await page.getByTestId('extensions-new').click();
	await page.getByTestId('new-skill-name').fill('bin-day');
	await page.getByTestId('new-skill-description').fill('Which bin goes out, and on which night.');
	await page.getByTestId('new-skill-create').click();
	await expect(page.getByTestId('ext-skill:bin-day')).toBeVisible();
});

test('a name that is nearly a path is refused, and says so in the dialog', async ({ page }) => {
	await open(page);
	await page.getByTestId('extensions-new').click();
	await page.getByTestId('new-skill-name').fill('../escape');
	await page.getByTestId('new-skill-description').fill('Nope.');
	await page.getByTestId('new-skill-create').click();
	await expect(page.getByTestId('new-skill-error')).toBeVisible();
	await expect(page.getByTestId('ext-skill:../escape')).toHaveCount(0);
});

// The catalogue — what an entry asks for, the two-step install, the hash and
// every program named — is `catalogue.spec.ts` since M65: it moved out of this
// fold to the top of the page, where a person can find it.
