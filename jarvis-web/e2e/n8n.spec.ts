import { expect, test } from '@playwright/test';

// M77: the house's n8n on Settings › Tools — one line that says whether it
// answers, or what to set. Against the mock it is not configured, which is
// what a fresh install shows.
test('Settings › Tools says n8n is not configured, and what to set', async ({ page }) => {
	await page.goto('/settings/tools');
	const line = page.getByTestId('n8n-connection');
	await expect(line).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('n8n-state')).toContainText(/not configured/i, { timeout: 10_000 });
	await expect(line).toContainText('N8N_API_KEY');
});
