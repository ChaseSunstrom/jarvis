import { test, expect } from '@playwright/test';

// Full round trip against the built app + mock backend (see serve-e2e.mjs):
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

// /api/tts attaches the server-held admin token to whatever it fetches, so its
// allow-list is a security boundary. A `path.includes('..')` test is not one:
// the URL parser collapses %2e%2e too, so the encoded form below used to return
// 200 with the backend's token-protected payload.
test('the tts proxy only reaches media paths', async ({ request }) => {
	// The real thing still works.
	const good = await request.get('/api/tts?path=/api/tts_proxy/test.mp3');
	expect(good.status()).toBe(200);
	expect(good.headers()['content-type']).toContain('audio');

	for (const path of [
		'/api/tts_proxy/../../_test/protected', // literal
		'/api/tts_proxy/%252e%252e/%252e%252e/_test/protected', // percent-encoded
		'/api/tts_proxy/%252E%252E/%252E%252E/_test/protected',
		'/api/tts_proxy/.%252e/.%252e/_test/protected',
		'/_test/protected',
		'//127.0.0.1:1/api/tts_proxy/a.wav'
	]) {
		const res = await request.get(`/api/tts?path=${path}`);
		expect(res.status(), path).toBe(400);
		expect(await res.text(), path).not.toContain('admin-only-payload');
	}
});

// --- management UI ---------------------------------------------------------

test('console nav links the HUD to the management pages', async ({ page }) => {
	await page.goto('/');
	await page.getByTestId('console-link').click();
	await expect(page).toHaveURL(/\/devices$/);

	for (const [testid, path] of [
		['nav-areas', '/areas'],
		['nav-automations', '/automations'],
		['nav-tools', '/tools'],
		['nav-settings', '/settings'],
		['nav-devices', '/devices']
	] as const) {
		await page.getByTestId(testid).click();
		await expect(page).toHaveURL(new RegExp(`${path}$`));
	}

	// and back to the voice HUD
	await page.getByTestId('hud-link').click();
	await expect(page.getByTestId('ptt')).toBeVisible();
});

test('devices page groups entities by area and a toggle round-trips call_service', async ({
	page
}) => {
	await page.goto('/devices');

	// Grouped under the area the entity registry puts it in.
	const lab = page.getByTestId('area-lab');
	await expect(lab).toBeVisible({ timeout: 15_000 });
	await expect(lab).toContainText('Lab');
	await expect(lab.getByTestId('entity-light.lab_lights')).toHaveCount(1);
	await expect(page.getByTestId('entity-light.lab_lights')).toContainText('Lab Lights');

	// Entities whose area comes from their device land in the same bucket.
	await expect(lab.getByTestId('entity-sensor.lab_temperature')).toHaveCount(1);
	// Areas the registry knows are rendered even for non-device entities.
	await expect(page.getByTestId('area-garage')).toContainText('Garage Door');

	const pill = page.getByTestId('state-light.lab_lights');
	await expect(pill).toHaveText('off');

	// click -> call_service over the ws relay -> mock mutates -> state_changed
	// arrives on the subscribe_events subscription -> DOM updates.
	await page.getByTestId('toggle-light.lab_lights').click();
	await expect(pill).toHaveText('on', { timeout: 10_000 });
	await expect(page.getByTestId('toggle-light.lab_lights')).toHaveText('TURN OFF');

	await page.getByTestId('toggle-light.lab_lights').click();
	await expect(pill).toHaveText('off', { timeout: 10_000 });

	// cover buttons and the climate setpoint use the same path
	await page.getByTestId('open-cover.garage_door').click();
	await expect(page.getByTestId('state-cover.garage_door')).toHaveText('open', { timeout: 10_000 });
	await page.getByTestId('close-cover.garage_door').click();
	await expect(page.getByTestId('state-cover.garage_door')).toHaveText('closed', {
		timeout: 10_000
	});

	await page.getByTestId('play-media_player.speaker').click();
	await expect(page.getByTestId('state-media_player.speaker')).toHaveText('playing', {
		timeout: 10_000
	});
	await page.getByTestId('pause-media_player.speaker').click();
	await expect(page.getByTestId('state-media_player.speaker')).toHaveText('paused', {
		timeout: 10_000
	});

	await expect(page.getByTestId('error')).toHaveCount(0);

	// filtering narrows the grouped list
	await page.getByTestId('filter').fill('garage');
	await expect(page.getByTestId('entity-cover.garage_door')).toBeVisible();
	await expect(page.getByTestId('entity-light.lab_lights')).toHaveCount(0);
});

test('areas page creates, renames and deletes an area', async ({ page }) => {
	await page.goto('/areas');
	await expect(page.getByTestId('area-lab')).toBeVisible({ timeout: 15_000 });

	await page.getByTestId('new-area-name').fill('Test Bay');
	await page.getByTestId('create-area').click();
	const bay = page.getByTestId('area-test_bay');
	await expect(bay).toBeVisible({ timeout: 10_000 });

	await page.getByTestId('rename-test_bay').fill('Test Bay Two');
	await page.getByTestId('save-test_bay').click();
	await expect(bay).toContainText('Test Bay Two', { timeout: 10_000 });

	// assigning an entity moves it out of the unassigned bucket
	await page
		.getByTestId('assign-automation.night_mode')
		.selectOption({ value: 'test_bay' });
	await expect(bay.getByTestId('assign-automation.night_mode')).toHaveCount(1, {
		timeout: 10_000
	});

	await page.getByTestId('delete-test_bay').click();
	await expect(bay).toHaveCount(0, { timeout: 10_000 });
	await expect(page.getByTestId('error')).toHaveCount(0);
});

test('automations page shows last_triggered, toggles and runs now', async ({ page }) => {
	await page.goto('/automations');
	const row = page.getByTestId('automation-automation.night_mode');
	await expect(row).toBeVisible({ timeout: 15_000 });
	await expect(row).toContainText('Night Mode');

	await expect(page.getByTestId('last-automation.morning_lights')).toHaveText('never');
	await expect(page.getByTestId('state-automation.night_mode')).toHaveText('on');

	await page.getByTestId('toggle-automation.night_mode').click();
	await expect(page.getByTestId('state-automation.night_mode')).toHaveText('off', {
		timeout: 10_000
	});
	await page.getByTestId('toggle-automation.night_mode').click();
	await expect(page.getByTestId('state-automation.night_mode')).toHaveText('on', {
		timeout: 10_000
	});

	await page.getByTestId('trigger-automation.morning_lights').click();
	await expect(page.getByTestId('flash')).toContainText('triggered', { timeout: 10_000 });
	await expect(page.getByTestId('last-automation.morning_lights')).not.toHaveText('never', {
		timeout: 10_000
	});
	await expect(page.getByTestId('error')).toHaveCount(0);
});

test('tools page degrades to the service catalogue and test-runs a tool', async ({ page }) => {
	await page.goto('/tools');

	// The mock answers unknown_command for jarvis/tools/list — the page must
	// explain that and fall back rather than break.
	await expect(page.getByTestId('hint')).toContainText('jarvis/tools/list', { timeout: 15_000 });
	await expect(page.getByTestId('tool-light.turn_on')).toBeVisible();

	await page.getByTestId('tool-select').selectOption('switch.turn_on');
	await page.getByTestId('tool-args').fill('{"entity_id": "switch.desk_fan"}');
	await page.getByTestId('tool-run').click();
	await expect(page.getByTestId('tool-result')).toContainText('changed_states', { timeout: 10_000 });
	await expect(page.getByTestId('tool-result')).toContainText('switch.desk_fan');

	// bad JSON is reported, not thrown
	await page.getByTestId('tool-args').fill('{not json');
	await page.getByTestId('tool-run').click();
	await expect(page.getByTestId('error')).toContainText('not valid JSON');

	// exposure toggle writes through the entity registry
	const expose = page.getByTestId('expose-light.lab_lights');
	await expect(expose).toHaveText('EXPOSED');
	await expose.click();
	await expect(expose).toHaveText('HIDDEN', { timeout: 10_000 });
	await expose.click();
	await expect(expose).toHaveText('EXPOSED', { timeout: 10_000 });
});

test('settings page reports the selected backend and streams events', async ({ page }) => {
	await page.goto('/settings');

	// serve-e2e.mjs points JARVIS_URL at the mock and HA_URL at a dead port, so
	// seeing the mock's url here proves JARVIS_* took precedence.
	await expect(page.getByTestId('backend-kind')).toHaveText('core', { timeout: 15_000 });
	await expect(page.getByTestId('backend-url')).toContainText('127.0.0.1');
	await expect(page.getByTestId('backend-token')).toContainText('held server-side');
	await expect(page.getByTestId('config-problem')).toHaveCount(0);
	await expect(page.getByTestId('tts-voice')).toHaveText('en_GB-alan-medium');

	// the live event stream fills once something moves
	await expect(page.getByTestId('live-filter')).toHaveText('state_changed');
	const page2 = await page.context().newPage();
	await page2.goto('/devices');
	await page2.getByTestId('toggle-switch.desk_fan').click({ timeout: 15_000 });
	await expect(page.getByTestId('event-log')).toContainText('switch.desk_fan', { timeout: 15_000 });
	await page2.getByTestId('toggle-switch.desk_fan').click();
	await page2.close();
});
