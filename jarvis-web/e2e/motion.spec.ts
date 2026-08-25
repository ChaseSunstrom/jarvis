import { test, expect } from '@playwright/test';

/**
 * M44's hard constraints, measured rather than asserted.
 *
 * Three things a motion system has to prove about itself, none of which can be
 * proved by reading the CSS:
 *
 *   1. It holds a frame budget — a measurement, with the long frames counted.
 *   2. `prefers-reduced-motion: reduce` actually stops the movement, including
 *      the boot sequence, which cannot be reduced to a fast transition and has
 *      to be skipped outright.
 *   3. It never blocks interaction. An animation somebody has to wait out is
 *      worse than no animation at all.
 */

const freshBoot = async (page: import('@playwright/test').Page) => {
	await page.addInitScript(() => sessionStorage.removeItem('jarvis:boot-played'));
};

/**
 * Count the gaps between presented frames, in the page.
 *
 * rAF rather than a DevTools trace: what matters is the interval between
 * frames the compositor actually presented, and this records exactly that.
 * The first frames after a navigation are parse, style and layout, so they are
 * dropped — they are not what any of this is about.
 */
const frameGaps = async (page: import('@playwright/test').Page, samples = 120) => {
	const frames = await page.evaluate(
		async (n) =>
			await new Promise<number[]>((resolve) => {
				const gaps: number[] = [];
				let last = performance.now();
				const tick = () => {
					const now = performance.now();
					gaps.push(now - last);
					last = now;
					if (gaps.length >= n) resolve(gaps);
					else requestAnimationFrame(tick);
				};
				requestAnimationFrame(tick);
			}),
		samples
	);
	const settled = frames.slice(5);
	// 34ms is two frames at 60Hz.
	return { settled, long: settled.filter((gap) => gap > 34), worst: Math.max(...settled) };
};

test('the interface holds its frame budget while things are moving', async ({ page }) => {
	// A CONTROL first: the same measurement on a settled, still page in the
	// same browser, seconds apart.
	//
	// An absolute threshold measures the host, not the app. This suite runs on
	// four shared vCPUs with no GPU, alongside Whisper and a compose stack, and
	// an absolute count went red at 19 long frames in a full-suite run and
	// green at 2 on its own — same code, same browser, different neighbours.
	// A threshold like that is one people re-run rather than read. Subtracting
	// a control taken moments earlier asks the question the milestone actually
	// asks: does OUR motion drop frames that a still page would not?
	await page.goto('/settings');
	await page.waitForTimeout(500);
	const still = await frameGaps(page);

	await freshBoot(page);
	await page.goto('/');
	const moving = await frameGaps(page);

	// The budget: no more than a tenth of frames long, and never materially
	// worse than the still page — five frames of headroom over the control, so
	// a host hiccup during either sample cannot fail it on its own.
	const detail =
		`moving: ${moving.long.length} long of ${moving.settled.length}, worst ` +
		`${moving.worst.toFixed(1)}ms · still: ${still.long.length} long of ` +
		`${still.settled.length}, worst ${still.worst.toFixed(1)}ms`;
	expect(moving.long.length, detail).toBeLessThanOrEqual(
		Math.max(Math.ceil(moving.settled.length * 0.1), still.long.length + 5)
	);
});

test('the page does not shift under somebody while it animates', async ({ page }) => {
	await freshBoot(page);
	await page.goto('/');
	const shifted = await page.evaluate(async () => {
		let total = 0;
		const observer = new PerformanceObserver((list) => {
			for (const entry of list.getEntries()) {
				const shift = entry as PerformanceEntry & { value: number; hadRecentInput: boolean };
				if (!shift.hadRecentInput) total += shift.value;
			}
		});
		observer.observe({ type: 'layout-shift', buffered: true });
		await new Promise((resolve) => setTimeout(resolve, 2500));
		observer.disconnect();
		return total;
	});
	// 0.1 is Google's "good" CLS. An entrance animation that moved the page
	// under a finger would blow through it.
	expect(shifted, `cumulative layout shift ${shifted}`).toBeLessThan(0.1);
});

test('the boot sequence never blocks interaction', async ({ page }) => {
	await freshBoot(page);
	await page.goto('/');
	// Immediately, not after the animation: whatever is on screen has to be
	// reachable while it is on screen.
	const composer = page.getByTestId('text-input');
	await composer.click({ timeout: 3000 });
	await composer.fill('typed during the boot sequence');
	await expect(composer).toHaveValue('typed during the boot sequence');
});

test.describe('with reduced motion', () => {
	test('nothing animates, and the interface still works', async ({ page }) => {
		await freshBoot(page);
		// `emulateMedia` rather than the `reducedMotion` context option: the
		// option did not reach the page in this project's config —
		// `matchMedia('(prefers-reduced-motion: reduce)')` was false — and a
		// reduced-motion test that is not actually reducing motion is the
		// worst possible outcome, because it passes.
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await page.goto('/');
		expect(
			await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches),
			'the preference is not actually being emulated'
		).toBe(true);
		await page.waitForLoadState('networkidle');

		const composer = page.getByTestId('text-input');
		await expect(composer).toBeVisible({ timeout: 5000 });
		await composer.fill('reduced motion still types');
		await expect(composer).toHaveValue('reduced motion still types');

		// Nothing is mid-animation: the global rule collapses every duration,
		// so no element reports a running animation.
		const running = await page.evaluate(
			() => document.getAnimations().filter((a) => a.playState === 'running').length
		);
		expect(running, `${running} animations still running under reduced motion`).toBe(0);
	});
});
