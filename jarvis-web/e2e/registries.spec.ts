import { expect, test } from '@playwright/test';

/**
 * M108 — the catalogue lists the registries beside the shipped skills.
 *
 * The mock lists two skills from `anthropic-skills` and two servers from
 * `mcp-registry`, and reports three servers the house skipped. INSTALL on
 * an MCP server is a plan — a URL and a tier, nothing downloaded — and
 * confirming it adds the server to the MCP servers fold.
 */
test.beforeEach(async ({ page }) => {
	await page.goto('/settings/tools');
	await page.getByTestId('catalog-canvas-design').waitFor({ timeout: 15_000 });
});

test('the registries are sources: a skill from Anthropic and a server from the MCP registry, each saying which', async ({ page }) => {
	const skill = page.getByTestId('catalog-canvas-design');
	await expect(skill.getByTestId('catalog-kind-canvas-design')).toHaveText('SKILL');
	await expect(skill.getByTestId('catalog-perms-canvas-design')).toContainText('anthropic-skills');
	const server = page.getByTestId('catalog-ac.tandem-docs-mcp');
	await expect(server.getByTestId('catalog-kind-ac.tandem-docs-mcp')).toHaveText('MCP');
	await expect(server.getByTestId('catalog-perms-ac.tandem-docs-mcp')).toContainText('mcp-registry');
	await expect(page.getByTestId('catalogue-skipped')).toContainText('3 servers');
});

test('searching narrows to the registry entry named', async ({ page }) => {
	// The page's one search box, as catalogue.spec.ts finds it.
	const box = page.locator('main [data-jv-filter]');
	await box.fill('weather');
	await expect(page.getByTestId('catalog-io.github.example-weather')).toBeVisible();
	await expect(page.getByTestId('catalog-canvas-design')).toHaveCount(0);
});

test('INSTALL on an MCP server is a plan with the URL and the tier, and confirming adds the server', async ({ page }) => {
	await page.getByTestId('catalog-install-ac.tandem-docs-mcp').click();
	const plan = page.getByTestId('install-plan');
	await expect(plan).toHaveAttribute('data-kind', 'mcp');
	await expect(page.getByTestId('install-url')).toHaveText('https://tandem.ac/mcp');
	await expect(page.getByTestId('install-tier')).toHaveText('2');
	await expect(page.getByTestId('install-note')).toContainText('nothing is downloaded');
	await page.getByTestId('install-confirm').click();
	await expect(page.getByTestId('install-plan')).toHaveCount(0);
	await expect(page.getByText('ac-tandem-docs-mcp').first()).toBeVisible();
});

test('INSTALL on a registry skill is the usual plan: files, checksum, and what it asks for', async ({ page }) => {
	await page.getByTestId('catalog-install-canvas-design').click();
	const plan = page.getByTestId('install-plan');
	await expect(plan).toHaveAttribute('data-kind', 'skill');
	await expect(plan).toContainText('anthropic-skills at 0123456789ab');
	await expect(page.getByTestId('install-permissions')).toHaveText('nothing');
});
