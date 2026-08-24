import { test, expect } from '@playwright/test';

/**
 * The style guide is a page, and pages break.
 *
 * `/styleguide` renders every token from the generated table. What this pins is
 * that it renders — one section per token group, each holding rows — and it
 * keeps a screenshot under `.verify/` so a design review has the page as it
 * shipped, not as somebody remembers it. The token lint is what checks that the
 * page itself types no colour; this checks the page is there to look at.
 */
test('the style guide renders every token group', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	// The console plays its boot sequence once per session over the whole
	// viewport; a screenshot taken under it is a screenshot of the overlay.
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto('/styleguide');
	await page.locator('[data-testid="boot"]').waitFor({ state: 'detached', timeout: 10_000 }).catch(() => {});
	await expect(page.getByRole('heading', { level: 1 })).toHaveText('Every token, rendered');
	for (const group of ['color', 'type', 'space', 'radius', 'elevation', 'motion', 'chrome']) {
		const section = page.locator(`[data-tokens="${group}"]`);
		await expect(section, group).toBeVisible();
		expect(await section.locator('li, .family, .btn, .panel').count(), `${group} rows`).toBeGreaterThan(0);
	}
	// Every component of the library is on the page, live.
	const gallery = page.locator('[data-components]');
	await expect(gallery).toBeVisible();
	await expect(gallery.getByTestId('reactor').first()).toBeVisible();

	// And ScreenState really drives all four states, not four static pictures.
	for (const state of ['loading', 'empty', 'error', 'offline'] as const) {
		await page.getByTestId(`state-${state}`).click();
		await expect(page.locator(`[data-screen-state="${state}"]`), state).toBeVisible();
	}
	await page.getByTestId('state-ready').click();
	await page.screenshot({ path: '../.verify/styleguide.png', fullPage: true });
});
