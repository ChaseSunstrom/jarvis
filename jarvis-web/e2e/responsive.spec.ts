import { test, expect } from '@playwright/test';
import { SCREENS } from '../src/lib/screens';

/**
 * Nothing scrolls sideways, at any width anybody uses.
 *
 * `html, body { overflow-x: hidden }` used to sit in `base.css` and made this
 * untestable: a row 40px too wide at 360px did not scroll, it was clipped, so
 * the page looked correct and the content was simply gone. The rule is off, and
 * this is what replaces it — wide things (tables, diffs, the nav) scroll inside
 * their own container, and the page itself never does.
 *
 * Five widths: a small phone, a large phone, a tablet, a laptop, a desktop.
 */
const WIDTHS = [
	{ width: 360, height: 780, name: 'small phone' },
	{ width: 414, height: 896, name: 'large phone' },
	{ width: 768, height: 1024, name: 'tablet' },
	{ width: 1024, height: 768, name: 'laptop' },
	{ width: 1440, height: 900, name: 'desktop' }
];

for (const size of WIDTHS) {
	test(`nothing overflows or is crushed at ${size.width}px (${size.name})`, async ({ page }) => {
		await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
		await page.setViewportSize({ width: size.width, height: size.height });

		for (const screen of SCREENS) {
			await page.goto(screen.path);
			await expect(page.getByTestId(screen.probe), `${screen.name} at ${size.width}`).toBeVisible({
				timeout: 15_000
			});
			const overflow = await page.evaluate(() => ({
				scrollWidth: document.documentElement.scrollWidth,
				clientWidth: document.documentElement.clientWidth,
				// Which element is the culprit, so a failure names it rather than
				// leaving somebody to bisect the page by hand.
				widest: [...document.querySelectorAll('body *')]
					.map((el) => ({
						tag: `${el.tagName.toLowerCase()}.${(el.className || '').toString().split(' ')[0]}`,
						right: Math.round(el.getBoundingClientRect().right)
					}))
					.filter((e) => e.right > document.documentElement.clientWidth + 1)
					.slice(0, 3)
			}));
			expect(
				overflow.scrollWidth,
				`${screen.name} at ${size.width}px overflows by ${overflow.scrollWidth - overflow.clientWidth}px; widest: ${JSON.stringify(overflow.widest)}`
			).toBeLessThanOrEqual(overflow.clientWidth + 1);

			// The other way a layout breaks at a narrow width: not overflowing,
			// but CRUSHED. A flex row whose buttons take their natural width
			// squeezes the sentence beside them to a two-character column, and
			// a paragraph rendered one letter per line reads as damage while
			// scrolling nothing sideways. The Extensions panel did exactly this
			// at 390px and only a screenshot caught it.
			const crushed = await page.evaluate(() => {
				const narrow: { text: string; width: number }[] = [];
				for (const el of document.querySelectorAll('p, li, dd')) {
					const text = (el.textContent || '').trim();
					if (text.length < 60) continue;
					const rect = el.getBoundingClientRect();
					if (rect.width === 0 && rect.height === 0) continue;
					if (rect.width < 140) narrow.push({ text: text.slice(0, 40), width: Math.round(rect.width) });
				}
				return narrow.slice(0, 3);
			});
			expect(
				crushed,
				`${screen.name} at ${size.width}px has prose squeezed into a sliver: ${JSON.stringify(crushed)}`
			).toEqual([]);
		}
	});
}
