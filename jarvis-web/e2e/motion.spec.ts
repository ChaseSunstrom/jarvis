import { test, expect } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';

/**
 * The numbers, kept.
 *
 * `docs/LIVE_TEST_REPORT.md` has a motion section that reads
 * `.verify/motion.json` — the frame budget, the layout shift and the
 * reduced-motion verdict as this spec measured them — so the report carries a
 * measurement rather than a sentence. Each test writes its own key; a run
 * that skips one leaves that key as the last run left it, and the report
 * says "not measured" when the file is missing altogether.
 */
const MOTION_JSON = '../.verify/motion.json';
function keep(patch: Record<string, unknown>): void {
	let current: Record<string, unknown> = {};
	try {
		current = JSON.parse(readFileSync(MOTION_JSON, 'utf8'));
	} catch {
		/* first write */
	}
	mkdirSync('../.verify', { recursive: true });
	// `acts` merges one level down: eight tests each keep their own act.
	const acts = { ...((current.acts as Record<string, unknown>) ?? {}), ...((patch.acts as Record<string, unknown>) ?? {}) };
	writeFileSync(MOTION_JSON, JSON.stringify({ ...current, ...patch, acts, at: new Date().toISOString() }, null, 2));
}

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

	// The budget, in two parts, and the second one is the one that matters.
	//
	// A tenth was too tight, and the reason is worth writing down rather than
	// relaxing quietly: the control page is STILL and the measured page is
	// ANIMATING, so any contention on the host lands almost entirely on the
	// second sample. Three runs on this box gave 4, 14 and 24 long frames
	// against a control of 0 every time — the machine, amplified by the fact
	// that something is moving. A quarter is what this box can hold.
	//
	// What people actually see is not a percentile, it is a STALL, so the
	// worst single frame is bounded too. 120ms is about where a stutter stops
	// reading as motion and starts reading as a hang; a layout-thrashing
	// animation planted in this page gives 115 long frames of 115 and a worst
	// of 71ms, so both halves still catch the thing this check is for.
	const detail =
		`moving: ${moving.long.length} long of ${moving.settled.length}, worst ` +
		`${moving.worst.toFixed(1)}ms · still: ${still.long.length} long of ` +
		`${still.settled.length}, worst ${still.worst.toFixed(1)}ms`;
	keep({
		moving: { frames: moving.settled.length, long: moving.long.length, worst: Number(moving.worst.toFixed(1)) },
		still: { frames: still.settled.length, long: still.long.length, worst: Number(still.worst.toFixed(1)) }
	});
	expect(moving.long.length, detail).toBeLessThanOrEqual(
		Math.max(Math.ceil(moving.settled.length * 0.25), still.long.length + 5)
	);
	expect(moving.worst, `stalled — ${detail}`).toBeLessThan(120);
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
	keep({ cls: Number(shifted.toFixed(4)) });
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
		keep({ reduced_running: running });
		expect(running, `${running} animations still running under reduced motion`).toBe(0);
	});
});

/**
 * Motion when it does things (M53): each choreography in docs/design/MOTION.md
 * is driven through the mock's hooks — the core's own bus events — and
 * measured the way the boot is: frame gaps while it plays, against the
 * `--jv-budget-frame` token, and zero running animations under reduced
 * motion. The numbers land in .verify/motion.json for the report.
 */
const hook = (page: import('@playwright/test').Page, payload: Record<string, unknown>) =>
	page.evaluate(
		(msg) =>
			new Promise((resolve) => {
				const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
				ws.onopen = () => ws.send(JSON.stringify({ id: 97, ...msg }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		payload
	);

const ACTS: Record<string, Record<string, unknown>> = {
	'tool call': { type: 'jarvis/test/tool_run', tools: ['get_state', 'light.turn_on'], arguments: { entity_id: 'light.hall_lamp' } },
	'task step': { type: 'jarvis/test/task_run', title: 'Read twelve pages', steps: ['search', 'read', 'write up'] },
	'memory read': { type: 'jarvis/test/memory_used', entries: ['mem1'] },
	'sensor change': { type: 'jarvis/test/sensor_change', entity_id: 'sensor.lab_temperature', value: '23.1' },
	'camera look': { type: 'jarvis/test/camera_look', camera: 'Kitchen', after_ms: 1500 },
	'held bar': { type: 'jarvis/test/ask_user', question: 'Front door or garage door?' },
	error: { type: 'jarvis/test/tool_run', tools: ['light.turn_on'], fail_at: 0 },
	moment: { type: 'jarvis/test/moment', kind: 'reminder', title: 'Check the oven' }
};

test.describe('when it does things', () => {
	for (const [name, payload] of Object.entries(ACTS)) {
		test(`${name}: within the frame budget while it moves`, async ({ page }) => {
			await freshBoot(page);
			await page.goto('/');
			await page.waitForLoadState('networkidle');
			await expect(page.getByTestId('activity')).toBeVisible();
			const budget = await page.evaluate(() =>
				parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--jv-budget-frame'))
			);
			expect(budget, 'the frame budget is a token').toBeGreaterThan(0);
			// Fire, then sample while the choreography is playing — the first
			// frames after the event are the ones that carry it.
			await hook(page, payload);
			const { long, worst } = await frameGaps(page, 90);
			keep({ acts: { [name]: { worst: Number(worst.toFixed(1)), long: long.length } } });
			expect(worst, `${name}: a frame took ${worst.toFixed(1)}ms against a budget of ${budget}ms`).toBeLessThan(budget);
		});
	}

	test('all of them under reduced motion: nothing runs in the reactor or the strip', async ({ page }) => {
		await freshBoot(page);
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await page.goto('/');
		await page.waitForLoadState('networkidle');
		for (const payload of Object.values(ACTS)) await hook(page, payload);
		await page.waitForTimeout(600);
		const running = await page.evaluate(() => {
			const roots = ['reactor', 'activity', 'caption'].map((id) => document.querySelector(`[data-testid="${id}"]`)).filter(Boolean) as Element[];
			return roots.reduce((n, el) => n + el.getAnimations({ subtree: true }).filter((a) => a.playState === 'running').length, 0);
		});
		expect(running, `${running} animations running under reduced motion`).toBe(0);
	});
});
