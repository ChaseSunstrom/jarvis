import { test, expect } from '@playwright/test';

// Full round trip against the built app + mock HA (see serve-e2e.mjs):
// click push-to-talk -> mic (fake device) -> /ws proxy -> mock pipeline
// events -> transcript + streamed response rendered in the DOM.
test('push-to-talk round trip renders transcript and response', async ({ page }) => {
	const consoleLatencies: string[] = [];
	page.on('console', (msg) => {
		if (msg.text().includes('[jarvis] latencies')) consoleLatencies.push(msg.text());
	});

	await page.goto('/?e2e=1');
	// status label maps pipeline state to HUD copy: idle -> STANDBY
	await expect(page.getByTestId('status')).toContainText(/standby/i, { timeout: 10_000 });

	// ?e2e=1 makes the PTT auto-stop after 1.5 s, so a single click completes a run.
	await page.getByTestId('ptt').click();

	await expect(page.getByTestId('transcript')).toContainText('turn on the lab lights', {
		timeout: 15_000
	});
	await expect(page.getByTestId('response')).toContainText('Turning on the lab lights.', {
		timeout: 15_000
	});

	// latency readout shows measured timings
	await expect(page.getByTestId('latency')).toContainText('stt', { timeout: 10_000 });

	// no pipeline error surfaced
	await expect(page.getByTestId('error')).toHaveCount(0);
});

test('healthz endpoint responds', async ({ request }) => {
	const res = await request.get('/healthz');
	expect(res.status()).toBe(200);
	expect(await res.json()).toEqual({ status: 'ok' });
});
