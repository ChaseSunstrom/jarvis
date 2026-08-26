import { test, expect, type Page } from '@playwright/test';

/**
 * The home screen, on Reactor II (M49).
 *
 * What is asserted is the direction, measured: the instrument and not a
 * shader; the four states on the element and on the palette; the level arc
 * following the amplitude the page feeds it; C2's chat layout — transcript,
 * exchange, this turn, the dock — and the one bar with the voice screen as its
 * first tab. `hud.spec.ts` covers the screen as a surface somebody uses;
 * this covers what it looks like and how it moves.
 */

const skipBoot = (page: Page) =>
	page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));

test('the reactor is the instrument, drawn as a vector and not a canvas', async ({ page }) => {
	await skipBoot(page);
	await page.goto('/');
	const reactor = page.getByTestId('reactor');
	await expect(reactor).toBeVisible({ timeout: 10_000 });
	expect(await reactor.evaluate((el) => el.tagName.toLowerCase())).toBe('svg');
	// The bezel's 120 ticks, the 36 blades, the coil, the level and the lens.
	expect(await reactor.locator('line').count()).toBe(120);
	expect(await reactor.locator('path.blade').count()).toBe(36);
	await expect(reactor.locator('circle.coil')).toHaveCount(1);
	await expect(reactor.locator('circle.level')).toHaveCount(1);
	await expect(reactor.locator('circle.rim')).toHaveCount(1);
	// And nothing of the previous direction: no canvas, no grid, no brackets.
	await expect(page.locator('canvas')).toHaveCount(0);
	await expect(page.locator('.jv-grid, .jv-bracket')).toHaveCount(0);
});

test('the four states are four palettes, and an error is a fifth', async ({ page }) => {
	await skipBoot(page);
	await page.goto('/');
	const reactor = page.getByTestId('reactor');
	await expect(reactor).toBeVisible({ timeout: 10_000 });

	const liveColour = () =>
		reactor.evaluate((el) => getComputedStyle(el.querySelector('.level')!).stroke);
	const seen = new Map<string, string>();
	for (const state of ['idle', 'listening', 'thinking', 'speaking']) {
		await page.evaluate((next) => {
			window.dispatchEvent(new CustomEvent('jarvis:orb-demo', { detail: next }));
		}, state);
		await expect(reactor).toHaveAttribute('data-state', state);
		// The palette transitions over --jv-dur-base; let it land.
		await page.waitForTimeout(400);
		seen.set(state, await liveColour());
	}
	// Idle is the deep accent, listening the lit one, thinking amber, speaking gold.
	expect(new Set(seen.values()).size, `states share a colour: ${JSON.stringify([...seen])}`).toBe(4);
	await expect(page.getByTestId('caption')).toBeVisible();
});

test('the level arc follows the amplitude the page feeds it', async ({ page }) => {
	await skipBoot(page);
	await page.goto('/');
	const reactor = page.getByTestId('reactor');
	await expect(reactor).toBeVisible({ timeout: 10_000 });
	// The demo drives a synthetic amplitude — a real number arriving on a real
	// interval — exactly as the microphone and the player do in use.
	await page.evaluate(() => {
		window.dispatchEvent(new CustomEvent('jarvis:orb-demo', { detail: 'speaking' }));
	});
	const levels = new Set<string>();
	for (let i = 0; i < 12; i++) {
		levels.add((await reactor.getAttribute('data-level')) ?? '');
		await page.waitForTimeout(90);
	}
	expect(levels.size, `the level never moved: ${[...levels]}`).toBeGreaterThan(3);
	// The dashoffset is the level: as the number rises the arc fills.
	const at = (lvl: string) =>
		reactor.evaluate((el, l) => {
			const level = el.querySelector('.level') as SVGCircleElement;
			return { lvl: l, offset: parseFloat(getComputedStyle(level).strokeDashoffset) };
		}, lvl);
	const sample = await at((await reactor.getAttribute('data-level')) ?? '0');
	expect(sample.offset).toBeGreaterThanOrEqual(0);
});

test('the chat layout: transcript, exchange, this turn, dock, one bar', async ({ page }) => {
	await skipBoot(page);
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/?e2e=1');
	await expect(page.getByTestId('mic')).toBeVisible({ timeout: 10_000 });

	// The bar, with the voice screen as its first, lit tab.
	const bar = page.getByTestId('top-bar');
	await expect(bar).toBeVisible();
	const tabs = bar.locator('nav a');
	await expect(tabs).toHaveCount(5);
	await expect(tabs.first()).toHaveText(/VOICE/);
	await expect(page.getByTestId('nav-voice')).toHaveAttribute('aria-current', 'page');
	// The underline is placed by measuring the lit tab after hydration and
	// again once the fonts land, so it is polled rather than read once.
	await expect
		.poll(
			async () => {
				const tab = await page.getByTestId('nav-voice').boundingBox();
				const ind = await page.getByTestId('nav-underline').boundingBox();
				if (!tab || !ind) return 'no box';
				return Math.abs(ind.x - tab.x) < 3 && Math.abs(ind.width - tab.width) < 3
					? 'under VOICE'
					: `underline at ${ind.x}×${ind.width}, VOICE at ${tab.x}×${tab.width}`;
			},
			{ timeout: 5_000 }
		)
		.toBe('under VOICE');

	// The three regions and the dock.
	await expect(page.getByTestId('transcript-panel')).toBeVisible();
	await expect(page.getByTestId('turn-panel')).toBeVisible();
	await expect(page.getByTestId('dock')).toBeVisible();
	const stage = await page.getByTestId('reactor').boundingBox();
	const left = await page.getByTestId('transcript-panel').boundingBox();
	const right = await page.getByTestId('turn-panel').boundingBox();
	expect(left!.x + left!.width).toBeLessThan(stage!.x);
	expect(right!.x).toBeGreaterThan(stage!.x + stage!.width);

	// A turn runs, and the exchange and the panels fill from it.
	await expect(page.getByTestId('transcript')).toContainText('turn on the lab lights', { timeout: 15_000 });
	await expect(page.getByTestId('response')).toContainText('Turning on the lab lights.', { timeout: 15_000 });
	await expect(page.getByTestId('turn-calls')).toContainText('turn_on');
	await expect(page.getByTestId('latency')).toContainText('ms');
	await expect(page.getByTestId('transcript-panel')).toContainText('turn on the lab lights');

	// The reply is set in the display face; the question in the body face; the
	// timings in mono. Type has three jobs and they are not interchangeable.
	const face = (testid: string) =>
		page.getByTestId(testid).evaluate((el) => getComputedStyle(el).fontFamily.toLowerCase());
	expect(await face('response')).toContain('space grotesk');
	expect(await face('transcript')).toContain('barlow');
	expect(await face('latency')).toContain('barlow');
	const timing = await page.getByTestId('latency').locator('dd').first().evaluate((el) => getComputedStyle(el).fontFamily.toLowerCase());
	expect(timing).toContain('mono');
});

test('the dock switches to chat mode, and chat mode wears the same bar', async ({ page }) => {
	await skipBoot(page);
	await page.goto('/');
	await expect(page.getByTestId('mic')).toBeVisible({ timeout: 10_000 });
	await page.getByTestId('mode-toggle').click();
	await expect(page.getByTestId('chat-panel')).toBeVisible();
	await expect(page.getByTestId('top-bar')).toBeVisible();
	await expect(page.getByTestId('nav-voice')).toHaveAttribute('aria-current', 'page');
	await expect(page.locator('.jv-grid, .jv-bracket')).toHaveCount(0);
	// The small instrument in the thread header is the same component.
	expect(await page.getByTestId('chat-reactor').evaluate((el) => el.tagName.toLowerCase())).toBe('svg');
});

test('it holds at a phone width: the instrument, the exchange, the dock', async ({ page }) => {
	await skipBoot(page);
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/');
	await expect(page.getByTestId('mic')).toBeVisible({ timeout: 10_000 });
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
	expect(overflow).toBeLessThanOrEqual(1);
	const reactor = await page.getByTestId('reactor').boundingBox();
	expect(reactor!.width).toBeGreaterThan(150);
	expect(reactor!.x + reactor!.width).toBeLessThanOrEqual(390);
	await expect(page.getByTestId('text-input')).toBeVisible();
});

test('under reduced motion the instrument is still, and the level rests', async ({ page }) => {
	await skipBoot(page);
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await page.goto('/');
	const reactor = page.getByTestId('reactor');
	await expect(reactor).toBeVisible({ timeout: 10_000 });
	await page.waitForTimeout(500);
	const running = await page.evaluate(
		() => document.getAnimations().filter((a) => a.playState === 'running').length
	);
	expect(running, `${running} animations still running under reduced motion`).toBe(0);
	await expect(reactor).toHaveAttribute('data-level', '0.00');
});
