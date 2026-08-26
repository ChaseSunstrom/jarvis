import { expect, test, type Page } from '@playwright/test';

// M76 — Jarvis in the middle, the tasks below.
//
// "should we move jarvis to be in the middle of the page instead of kind of
// near the top?" and "can we have the tasks popups show below jarvis on the
// voice page?" Both are geometry, so both are measured: at rest the
// instrument's centre sits in the middle band of the viewport, and a running
// task's dock draws under the instrument, never over it.

const tell = async (page: Page, message: Record<string, unknown>) =>
	page.evaluate(async (msg) => {
		const ws = new WebSocket(`ws://${location.host}/api/websocket`);
		await new Promise<void>((resolve) => ws.addEventListener('open', () => resolve()));
		ws.send(JSON.stringify({ type: 'auth', access_token: 'e2e' }));
		await new Promise((r) => setTimeout(r, 100));
		ws.send(JSON.stringify({ id: 77, ...msg }));
		await new Promise((r) => setTimeout(r, 200));
		ws.close();
	}, message);

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
		// Connected and settled: the offline and connecting states draw the
		// page differently, and a measurement of those is not of the layout.
		await expect(page.getByTestId('mic')).toContainText(/listening|muted/i, { timeout: 15_000 });
		await page.waitForTimeout(600);

		const reactor = await centreY(page, 'reactor');
		const band = reactor.centre / viewport.height;
		const facts = await page.evaluate(() => {
			const main = document.querySelector('main.voice') as HTMLElement;
			const box = (sel: string) => { const el = document.querySelector(sel) as HTMLElement | null; if (!el) return null; const b = el.getBoundingClientRect(); return { top: Math.round(b.top), h: Math.round(b.height) }; };
			return { rows: getComputedStyle(main).gridTemplateRows, main: box('main.voice'), stage: box('.stage'), exchange: box('.exchange'), dock: box('.dock'), state: main.dataset.state };
		});
		const why = `the instrument's centre is at ${Math.round(band * 100)}% of the height; ${JSON.stringify(facts)}`;
		expect(band, why).toBeGreaterThan(0.28);
		expect(band, why).toBeLessThan(0.72);

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
