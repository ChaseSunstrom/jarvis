import { test } from '@playwright/test';

/**
 * The taste checkpoint: four recordings, for a person to watch.
 *
 * `motion.spec.ts` can prove smooth, consistent and accessible. It cannot
 * prove that any of it feels good, and no test will — so this records the four
 * moments that carry the character of the interface and leaves them in
 * `docs/motion-review/` for somebody to look at and say what they think.
 *
 *     npx playwright test motion-review.spec.ts
 *
 * Not part of the gate. It writes videos; it asserts nothing.
 */

test.use({
	video: { mode: 'on', size: { width: 1280, height: 800 } },
	viewport: { width: 1280, height: 800 }
});

const fresh = async (page: import('@playwright/test').Page) => {
	await page.addInitScript(() => sessionStorage.removeItem('jarvis:boot-played'));
};

test('boot', async ({ page }) => {
	await fresh(page);
	await page.goto('/');
	// Long enough to see it finish and settle, rather than to see it start.
	await page.waitForTimeout(4000);
});

test('idle to listening to thinking to speaking', async ({ page }) => {
	await fresh(page);
	await page.goto('/');
	await page.waitForTimeout(1500);
	// The orb's four states, driven the way the app drives them: `orbState` and
	// a level. Faked here only in that nobody is speaking into a microphone —
	// the amplitude is a real number arriving on a real interval.
	for (const state of ['listening', 'thinking', 'speaking', 'idle']) {
		await page.evaluate((next) => {
			window.dispatchEvent(new CustomEvent('jarvis:orb-demo', { detail: next }));
		}, state);
		await page.waitForTimeout(2200);
	}
});

test('a task running', async ({ page }) => {
	await fresh(page);
	await page.goto('/tasks');
	await page.waitForTimeout(1200);
	await page.evaluate(async () => {
		const socket = new WebSocket(
			`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
		);
		await new Promise((resolve) => socket.addEventListener('open', resolve));
		socket.send(
			JSON.stringify({
				id: 1,
				type: 'jarvis/test/task_run',
				kind: 'code',
				title: 'Add an OFFLINE state to the settings screen',
				steps: ['read the route', 'wrap it in ScreenState', 'run the check'],
				tick_ms: 900
			})
		);
	});
	await page.waitForTimeout(6000);
});

test('moving between pages', async ({ page }) => {
	await fresh(page);
	await page.goto('/');
	await page.waitForTimeout(1500);
	for (const path of ['/tasks', '/memory', '/notes', '/']) {
		await page.goto(path);
		await page.waitForTimeout(1400);
	}
});
