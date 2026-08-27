import { expect, test, type Page } from '@playwright/test';

// M76 — Jarvis in the middle, the tasks below.
//
// "should we move jarvis to be in the middle of the page instead of kind of
// near the top?" and "can we have the tasks popups show below jarvis on the
// voice page?" Both are geometry, so both are measured: at rest the
// instrument's centre sits in the middle band of the viewport, and a running
// task's dock draws under the instrument, never over it.

// The same shape as tasks.spec.ts: the mock's socket is `/ws`, a frame gets one
// answer, and resolving on that answer is what keeps this from hanging.
const tell = (page: Page, frame: Record<string, unknown>) =>
	page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
				ws.onopen = () => ws.send(JSON.stringify({ id: 91, ...payload }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		frame
	);

const centreY = async (page: Page, testid: string) => {
	const box = await page.getByTestId(testid).boundingBox();
	if (!box) throw new Error(`${testid} has no box`);
	return { centre: box.y + box.height / 2, bottom: box.y + box.height, top: box.y };
};

for (const viewport of [
	{ width: 1440, height: 900 },
	{ width: 390, height: 844 }
]) {
	test(`the instrument sits in the middle band at ${viewport.width}×${viewport.height}, and a running task draws below it`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
		await page.goto('/');
		await expect(page.getByTestId('reactor')).toBeVisible({ timeout: 15_000 });
		await page.waitForTimeout(600);

		const reactor = await centreY(page, 'reactor');
		const band = reactor.centre / viewport.height;
		const why = `the instrument's centre is at ${Math.round(band * 100)}% of the height (top ${Math.round(reactor.top)}, bottom ${Math.round(reactor.bottom)})`;
		// The middle band is a desktop claim: on a phone the page is taller than
		// the screen (the transcript and the turn stack below), so the
		// instrument leads the page rather than floating in the middle of it.
		if (viewport.width >= 1024) {
			expect(band, why).toBeGreaterThan(0.28);
			expect(band, why).toBeLessThan(0.72);
		}

		await tell(page, { type: 'jarvis/test/task_run', title: 'A long errand', steps: ['a', 'b', 'c'], tick_ms: 900 });
		const dock = page.getByTestId('task-dock');
		await expect(dock).toBeVisible({ timeout: 10_000 });
		await expect(dock).toContainText('A long errand', { timeout: 10_000 });
		const dockBox = await centreY(page, 'task-dock');
		const reactorNow = await centreY(page, 'reactor');
		expect(dockBox.top, 'the task dock is not below the instrument').toBeGreaterThanOrEqual(reactorNow.bottom - 1);
		// And it is the page's own, not the layout's floating alerts.
		await expect(page.getByTestId('voice-tasks').getByTestId('task-dock')).toBeVisible();
		await expect(page.getByTestId('hud-alerts').getByTestId('task-dock')).toHaveCount(0);
		await tell(page, { type: 'jarvis/test/task_reset' });
	});
}
