import { expect, test } from '@playwright/test';

/**
 * M114 — SETTINGS › SYSTEM: every variable .env.example names, set from the
 * console and kept. The mock reads the repository's own .env.example, so
 * the list here is the list the documentation keeps; a secret is masked
 * until REVEAL; SET keeps a value and says it applies on restart; RESTART
 * makes it live; CLEAR forgets it.
 */
test.beforeEach(async ({ page }) => {
	await page.goto('/settings/system');
	await page.getByTestId('settings-system-lede').waitFor({ timeout: 15_000 });
});

test('the section lists the documented variables with their why, and masks a secret', async ({ page }) => {
	const tz = page.getByTestId('env-TZ');
	await expect(tz).toBeVisible();
	await expect(tz).toContainText('TZ');
	await expect(page.getByTestId('env-live-TZ')).toContainText("from the container's environment · America/Chicago");
	const token = page.getByTestId('env-JARVIS_TOKEN');
	await expect(token).toContainText('secret');
	await expect(page.getByTestId('env-live-JARVIS_TOKEN')).toContainText('••••');
	await expect(page.getByTestId('env-live-JARVIS_TOKEN')).not.toContainText('live-token-value');
	await expect(page.getByTestId('env-input-JARVIS_TOKEN')).toHaveAttribute('type', 'password');
	expect(await page.locator('[data-testid^="env-"][data-jv-row]').count()).toBeGreaterThanOrEqual(30);
});

test('SET keeps a value and says it applies on restart; RESTART makes it live; CLEAR forgets it', async ({ page }) => {
	await page.getByTestId('env-input-PIPER_VOICE').fill('en_US-lessac-medium');
	await page.getByTestId('env-set-PIPER_VOICE').click();
	await expect(page.getByTestId('env-pending-PIPER_VOICE')).toHaveText('applies on restart');
	await expect(page.getByTestId('env-live-PIPER_VOICE')).toContainText('set here: en_US-lessac-medium');
	await page.getByTestId('system-restart-button').click();
	await page.getByTestId('system-restart-confirm').click();
	await page.reload();
	await page.getByTestId('settings-system-lede').waitFor({ timeout: 15_000 });
	await expect(page.getByTestId('env-pending-PIPER_VOICE')).toHaveCount(0);
	await expect(page.getByTestId('env-live-PIPER_VOICE')).toContainText('set here, live since the last boot · en_US-lessac-medium');
	await page.getByTestId('env-clear-PIPER_VOICE').click();
	await expect(page.getByTestId('env-pending-PIPER_VOICE')).toHaveText('applies on restart');
	await expect(page.getByTestId('env-clear-PIPER_VOICE')).toHaveCount(0);
});

test('a secret set here is masked in the list and shown only by REVEAL', async ({ page }) => {
	await page.getByTestId('env-input-N8N_API_KEY').fill('n8n-key-9f9f');
	await page.getByTestId('env-set-N8N_API_KEY').click();
	await expect(page.getByTestId('env-live-N8N_API_KEY')).toContainText('set here: ••••');
	await expect(page.getByTestId('env-live-N8N_API_KEY')).not.toContainText('n8n-key-9f9f');
	await page.getByTestId('env-reveal-N8N_API_KEY').click();
	await expect(page.getByTestId('env-live-N8N_API_KEY')).toContainText('n8n-key-9f9f');
});

test('the filter narrows by name or words', async ({ page }) => {
	await page.getByTestId('env-filter').fill('piper');
	await expect(page.getByTestId('env-PIPER_VOICE')).toBeVisible();
	await expect(page.getByTestId('env-TZ')).toHaveCount(0);
});
