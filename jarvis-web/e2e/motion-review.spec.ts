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

test('jarvis at work', async ({ page }) => {
	// What the voice tab does while Jarvis works (M52/M53): a tool call sweeps
	// the blades and draws a call line; a sensor reading counts in; a camera
	// look irises the lens with "looking · Kitchen" under it; a fact is
	// remembered and the graph blinks; a moment lands; a task steps. Driven
	// through the mock's hooks, which fire the core's own bus events.
	await fresh(page);
	await page.goto('/');
	await page.waitForTimeout(1500);
	const hook = (payload: Record<string, unknown>) =>
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
	await hook({ type: 'jarvis/test/tool_run', tools: ['get_state', 'light.turn_on'], arguments: { entity_id: 'light.hall_lamp' } });
	await page.waitForTimeout(1400);
	await hook({ type: 'jarvis/test/sensor_change', entity_id: 'sensor.lab_temperature', value: '23.1' });
	await page.waitForTimeout(1200);
	await hook({ type: 'jarvis/test/camera_look', camera: 'Kitchen', after_ms: 2200 });
	await page.waitForTimeout(2600);
	await hook({ type: 'jarvis/test/memory_used', entries: ['mem1'] });
	await page.waitForTimeout(1600);
	await hook({ type: 'jarvis/test/moment', kind: 'reminder', title: 'Check the oven' });
	await page.waitForTimeout(1200);
	await hook({ type: 'jarvis/test/task_run', title: 'Read twelve pages', steps: ['search', 'read', 'write up'] });
	await page.waitForTimeout(2400);
});
