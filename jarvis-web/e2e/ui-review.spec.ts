import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { SCREENS } from '../src/lib/screens';

/**
 * A picture of every screen, at three widths, for a person to look at.
 *
 * Not an assertion. `states.spec.ts` proves each screen renders and handles
 * its states; this exists because M48's remaining question — is it clean, is
 * the hierarchy obvious, would a first-time user know what this is for — is
 * not one a test can answer, and the answer is worth having anyway.
 *
 * Skipped unless `UI_REVIEW=1`, because 39 screenshots is a minute nobody
 * needs on every run.
 */
const BREAKPOINTS = [
	{ name: 'mobile', width: 390, height: 844 },
	{ name: 'tablet', width: 834, height: 1112 },
	{ name: 'desktop', width: 1440, height: 900 }
];

const SHOTS = SCREENS.filter((screen) => !screen.path.includes('['));

test.describe('ui review', () => {
	test.skip(!process.env.UI_REVIEW, 'set UI_REVIEW=1');

	for (const screen of SHOTS) {
		test(`${screen.name} at three widths`, async ({ page }) => {
			await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
			const slug = screen.path === '/' ? 'hud' : screen.path.slice(1).replace(/\//g, '-');
			for (const size of BREAKPOINTS) {
				await page.setViewportSize({ width: size.width, height: size.height });
				await page.goto(screen.path);
				await expect(page.getByTestId(screen.probe)).toBeVisible({ timeout: 15_000 });
				// Let the entrance animations finish, so the picture is the page
				// rather than the page arriving.
				await page.waitForTimeout(700);
				const dir = `../docs/ui-review/${slug}`;
				mkdirSync(dir, { recursive: true });
				await page.screenshot({ path: `${dir}/${size.name}.png`, fullPage: true });
			}
		});
	}
});
