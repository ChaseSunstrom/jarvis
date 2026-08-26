import { test, expect, type Page } from '@playwright/test';
import { SCREENS } from '../src/lib/screens';
import { TOKENS } from '../src/lib/tokens';

/**
 * The look, measured (M50).
 *
 * `token_lint.py` proves no value was typed by hand; `states.spec.ts` proves
 * every screen renders and handles its states. Neither can tell a console on
 * the tokens from a console on the DIRECTION — M48 shipped monospace prose,
 * a technical grid, corner brackets and pill buttons drawn entirely in
 * `--jv-*` names. So this asks the rendered page what Reactor II asks of it:
 * what face the words are set in, what is in the DOM, what shape a control
 * has, what colour a panel is.
 */

const hex = (name: keyof typeof TOKENS) => TOKENS[name].toLowerCase();
const rgb = (h: string) => {
	const n = parseInt(h.slice(1), 16);
	return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
};

const gotoScreen = async (page: Page, path: string) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto(path);
};

const SHOTS = SCREENS.filter((screen) => !screen.path.includes('['));

for (const screen of SHOTS) {
	test(`${screen.name} is drawn to the direction`, async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await gotoScreen(page, screen.path);
		await expect(page.getByTestId(screen.probe)).toBeVisible({ timeout: 15_000 });
		await page.waitForTimeout(400);

		const facts = await page.evaluate(
			({ panel, bg, accent }) => {
				const lower = (s: string) => s.toLowerCase();
				const body = getComputedStyle(document.body);
				// Prose: every paragraph and list item with a sentence in it.
				const prose = [...document.querySelectorAll('p, li, dd, label, h1, h2, h3')].filter(
					(el) => (el.textContent || '').trim().length > 24 && (el as HTMLElement).offsetParent !== null
				);
				const monoProse = prose
					.filter((el) => lower(getComputedStyle(el).fontFamily).includes('mono'))
					// A pre/code line is data; a list of tool calls or ids is data.
					.filter((el) => !el.closest('pre, code, [data-mono], .calls, .k, dl'))
					.map((el) => (el.textContent || '').trim().slice(0, 40));
				// Pill-shaped controls: anything clickable whose radius is the pill.
				const pills = [...document.querySelectorAll('button, a, input, select, .pill, .tag')]
					.filter((el) => (el as HTMLElement).offsetParent !== null)
					.filter((el) => {
						const r = getComputedStyle(el).borderTopLeftRadius;
						const box = el.getBoundingClientRect();
						return parseFloat(r) >= 100 && box.width > 20;
					})
					.map((el) => `${el.tagName.toLowerCase()}.${(el.className || '').toString().split(' ')[0]}`);
				const glow = [...document.querySelectorAll('h1, h2, .logo, .brand, .word')]
					.filter((el) => getComputedStyle(el).textShadow !== 'none')
					.map((el) => el.tagName.toLowerCase());
				const panels = [...document.querySelectorAll('section, article, aside, .panel')]
					.filter((el) => (el as HTMLElement).offsetParent !== null)
					.map((el) => getComputedStyle(el).backgroundColor)
					.filter((c) => c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent');
				const offPalette = panels.filter((c) => c !== panel && c !== bg);
				return {
					bodyFont: lower(body.fontFamily),
					bodyBg: body.backgroundColor,
					grid: document.querySelectorAll('.jv-grid, .jv-bracket').length,
					canvas: document.querySelectorAll('canvas').length,
					monoProse,
					pills,
					glow,
					offPalette: [...new Set(offPalette)],
					underline: !!document.querySelector('[data-testid="nav-underline"]'),
					// Named, not counted: a second filled control on CI (01bfb30, six
					// screens at once, no web file changed) could not be told from
					// here without the name of the thing that was filled.
					accentButtons: [...document.querySelectorAll('button')]
						.filter(
							(b) => (b as HTMLElement).offsetParent !== null && getComputedStyle(b).backgroundColor === accent
						)
						.map((b) => b.getAttribute('data-testid') || (b.textContent || '').trim().slice(0, 40))
				};
			},
			{ panel: rgb(hex('--jv-panel')), bg: rgb(hex('--jv-bg')), accent: rgb(hex('--jv-accent')) }
		);

		expect(facts.bodyFont, 'the ground is not set in Barlow').toContain('barlow');
		expect(facts.bodyBg, 'the ground is not --jv-bg').toBe(rgb(hex('--jv-bg')));
		expect(facts.grid, 'the technical grid or the corner brackets are still drawn').toBe(0);
		expect(facts.canvas, 'a canvas is drawing something the tokens cannot see').toBe(0);
		expect(facts.monoProse, `prose set in mono: ${JSON.stringify(facts.monoProse)}`).toEqual([]);
		expect(facts.pills, `pill-shaped controls: ${JSON.stringify(facts.pills)}`).toEqual([]);
		expect(facts.glow, 'glowing text').toEqual([]);
		expect(facts.underline, 'the bar has no sliding underline').toBe(true);
		// Panels are the panel colour or the ground; nothing tints itself.
		expect(facts.offPalette, `panel backgrounds off the palette: ${JSON.stringify(facts.offPalette)}`).toEqual([]);
		// The accent is spent, not spread: at most one filled primary control at a time.
		expect(
			facts.accentButtons.length,
			`more than one filled accent control: ${JSON.stringify(facts.accentButtons)}`
		).toBeLessThanOrEqual(1);
	});
}

test('the section strip is a segmented control, not a second tab bar', async ({ page }) => {
	await gotoScreen(page, '/house/devices');
	await expect(page.getByTestId('section-devices')).toBeVisible({ timeout: 15_000 });
	const strip = page.locator('nav[aria-label="Sections"]');
	await expect(strip).toBeVisible();
	const facts = await strip.evaluate((el) => ({
		border: getComputedStyle(el).borderTopWidth,
		radius: parseFloat(getComputedStyle(el).borderTopLeftRadius),
		current: (() => {
			const a = el.querySelector('a[aria-current="page"]');
			return a ? getComputedStyle(a).backgroundColor : '';
		})()
	}));
	expect(facts.border).toBe('1px');
	expect(facts.radius).toBeLessThan(20);
	expect(facts.current, 'the current segment is not raised').toBe(rgb(hex('--jv-surface-2')));
});
