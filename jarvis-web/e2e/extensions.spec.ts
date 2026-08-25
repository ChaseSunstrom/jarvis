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

test('the catalog shows what an entry asks for before anything is installed', async ({ page }) => {
	await open(page);
	await page.getByTestId('extensions-browse').click();

	// Both entries, with their declared permissions on the row.
	await expect(page.getByTestId('catalog-bin-day')).toBeVisible();
	await expect(page.getByTestId('catalog-perms-friendly-helper')).toContainText('run_process');

	// The hostile description arrives wrapped, and it is shown as text rather
	// than obeyed — the marker is the visible evidence that it is data.
	await expect(page.getByTestId('catalog-friendly-helper')).toContainText('untrusted_content');
});

test('installing is a second decision, with the hash and every program named', async ({ page }) => {
	await open(page);
	await page.getByTestId('extensions-browse').click();
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
	await expect(page.getByTestId('ext-skill:friendly-helper')).toBeVisible();
});

test('a benign entry installs with no program to warn about', async ({ page }) => {
	await open(page);
	await page.getByTestId('extensions-browse').click();
	await page.getByTestId('catalog-install-bin-day').click();
	await expect(page.getByTestId('install-plan')).toBeVisible();
	await expect(page.getByTestId('install-hooks')).toHaveCount(0);
	await page.getByTestId('install-confirm').click();
	await expect(page.getByTestId('ext-skill:bin-day')).toBeVisible();
});
