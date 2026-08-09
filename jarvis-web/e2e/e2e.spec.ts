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
	// Status label maps pipeline state to HUD copy: idle -> STANDBY. The HUD
	// only says STANDBY once it has hydrated and opened its socket (before that
	// it says CONNECTING), so this doubles as the gate that stops the click
	// below from landing on a button with no handler bound yet. `goto` resolves
	// on `load`, which is earlier than that.
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

// --- chrome: boot sequence, motion, palette, shortcuts, toasts -------------

test('the boot sequence plays, never blocks a click, and runs once per session', async ({
	page
}) => {
	// `commit` returns before hydration, so the poll below starts early enough
	// to catch a 1.2 s overlay instead of racing it.
	await page.goto('/devices', { waitUntil: 'commit' });
	await expect(page.getByTestId('boot')).toBeAttached({ timeout: 10_000 });

	// The precise claim is "pointer-events: none": while the overlay is on
	// screen, a hit test at the nav's centre must still land on the nav. Simply
	// clicking would not prove it — Playwright would happily wait the animation
	// out and then click.
	const hit = await page.evaluate(() => {
		const nav = document.querySelector('[data-testid="nav-settings"]') as HTMLElement | null;
		if (!nav) return { bootUp: false, hitsNav: false };
		const r = nav.getBoundingClientRect();
		const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
		return {
			bootUp: Boolean(document.querySelector('[data-testid="boot"]')),
			hitsNav: Boolean(top && nav.contains(top))
		};
	});
	expect(hit.bootUp).toBe(true);
	expect(hit.hitsNav).toBe(true);

	// It dissolves on its own and marks the session.
	await expect(page.getByTestId('boot')).toHaveCount(0, { timeout: 10_000 });
	expect(await page.evaluate(() => sessionStorage.getItem('jarvis:boot-played'))).toBe('1');

	// And does not replay on the next load in the same session.
	await page.reload();
	await expect(page.getByTestId('nav-devices')).toBeVisible();
	await expect(page.getByTestId('boot')).toHaveCount(0);
});

test('prefers-reduced-motion skips the boot sequence entirely', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await page.goto('/devices', { waitUntil: 'commit' });
	await expect(page.getByTestId('nav-devices')).toBeVisible({ timeout: 10_000 });
	await expect(page.getByTestId('boot')).toHaveCount(0);
	// Not merely "gone quickly" — never shown at all.
	await page.waitForTimeout(400);
	await expect(page.getByTestId('boot')).toHaveCount(0);
	await page.emulateMedia({ reducedMotion: null });
});

test('route changes swap the console body and mark the current nav item', async ({ page }) => {
	await page.goto('/devices');
	const route = page.getByTestId('route');
	await expect(route).toHaveAttribute('data-route', '/devices');
	await expect(page.getByTestId('nav-devices')).toHaveAttribute('aria-current', 'page');

	await page.getByTestId('nav-automations').click();
	await expect(route).toHaveAttribute('data-route', '/automations');
	await expect(route).toContainText('AUTOMATIONS');
	await expect(page.getByTestId('nav-automations')).toHaveAttribute('aria-current', 'page');
	await expect(page.getByTestId('nav-devices')).not.toHaveAttribute('aria-current', 'page');

	// The transition wrapper is the thing the animation hangs off; it must
	// actually be in the tree, not an idea in a stylesheet.
	await expect(route).toHaveClass(/jv-route/);
});

test('the connection indicator reports the real websocket state', async ({ page }) => {
	await page.goto('/devices');
	await expect(page.getByTestId('link-status')).toHaveAttribute('data-status', 'connected', {
		timeout: 15_000
	});
	await expect(page.getByTestId('link-status')).toContainText('LINK OK');
});

test('the command palette opens from the keyboard, filters, and toggles an entity', async ({
	page
}) => {
	await page.goto('/devices');
	await expect(page.getByTestId('link-status')).toHaveAttribute('data-status', 'connected', {
		timeout: 15_000
	});
	await expect(page.getByTestId('state-light.lab_lights')).toHaveText('off');

	await page.keyboard.press('Control+k');
	await expect(page.getByTestId('palette')).toBeVisible();

	// Centred, and inside the viewport. `translateX(-50%)` plus an entrance
	// animation that ends on `transform: none` is an easy way to lose this.
	const box = (await page.getByTestId('palette').boundingBox())!;
	const viewport = page.viewportSize()!;
	expect(box.x).toBeGreaterThan(0);
	expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
	expect(Math.abs(box.x + box.width / 2 - viewport.width / 2)).toBeLessThan(2);

	// Esc closes it again.
	await page.keyboard.press('Escape');
	await expect(page.getByTestId('palette')).toHaveCount(0);

	await page.keyboard.press('Control+k');
	await page.getByTestId('palette-input').fill('lab lights');
	await expect(page.getByTestId('palette-item-entity:light.lab_lights')).toBeVisible();
	// Filtering is exclusive, not just a highlight.
	await expect(page.getByTestId('palette-item-entity:cover.garage_door')).toHaveCount(0);
	await expect(page.getByTestId('palette-hint')).toContainText('turn on');

	// Enter on a flippable entity performs the call and closes the palette.
	await page.keyboard.press('Enter');
	await expect(page.getByTestId('palette')).toHaveCount(0);
	await expect(page.getByTestId('state-light.lab_lights')).toHaveText('on', { timeout: 10_000 });
	await expect(page.getByTestId('toast').first()).toContainText('Lab Lights');

	// Put the world back the way the earlier tests left it.
	await page.keyboard.press('Control+k');
	await page.getByTestId('palette-input').fill('lab lights');
	await expect(page.getByTestId('palette-hint')).toContainText('turn off');
	await page.keyboard.press('Enter');
	await expect(page.getByTestId('state-light.lab_lights')).toHaveText('off', { timeout: 10_000 });
});

test('the command palette jumps to a page', async ({ page }) => {
	await page.goto('/devices');
	// data-status only becomes "connected" from client code, so this doubles as
	// "the page has hydrated" — a keystroke sent before that lands nowhere.
	await expect(page.getByTestId('link-status')).toHaveAttribute('data-status', 'connected', {
		timeout: 15_000
	});
	await page.keyboard.press('Control+k');
	await page.getByTestId('palette-input').fill('settings');
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(/\/settings$/);
});

test('keyboard shortcuts focus the filter and navigate', async ({ page }) => {
	await page.goto('/devices');
	await expect(page.getByTestId('link-status')).toHaveAttribute('data-status', 'connected', {
		timeout: 15_000
	});
	await expect(page.getByTestId('filter')).toBeVisible();

	await page.keyboard.press('/');
	expect(await page.evaluate(() => document.activeElement?.getAttribute('data-testid'))).toBe(
		'filter'
	);
	// `/` focused the field rather than being typed into it.
	await expect(page.getByTestId('filter')).toHaveValue('');

	// A bare letter inside a text field must stay a letter.
	await page.keyboard.type('gd');
	await expect(page).toHaveURL(/\/devices$/);
	await expect(page.getByTestId('filter')).toHaveValue('gd');

	await page.getByTestId('filter').fill('');
	await page.keyboard.press('Escape');
	await page.keyboard.press('g');
	await page.keyboard.press('a');
	await expect(page).toHaveURL(/\/automations$/);

	await page.keyboard.press('g');
	await page.keyboard.press('d');
	await expect(page).toHaveURL(/\/devices$/);
});

// The mock has a lock entity but no `lock` domain in its service catalogue —
// exactly the shape of "the UI offers a control the backend cannot perform".
// It used to fail in silence.
test('a rejected call_service raises a toast as well as an inline error', async ({ page }) => {
	await page.goto('/devices');
	const lock = page.getByTestId('lock-lock.front_door');
	await expect(lock).toBeVisible({ timeout: 15_000 });
	await page.getByTestId('filter').fill('front door');
	await expect(lock).toBeVisible();

	await lock.click();

	const toast = page.getByTestId('toast').first();
	await expect(toast).toBeVisible({ timeout: 10_000 });
	await expect(toast).toContainText('Lock failed');
	await expect(toast).toContainText('unknown service lock.lock');
	await expect(page.getByTestId('error')).toContainText('unknown service lock.lock');

	// It is dismissible, not just decorative.
	await page.getByTestId('toast-dismiss').first().click();
	await expect(page.getByTestId('toast')).toHaveCount(0);
});

test('the console is usable at phone width without sideways scrolling', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/devices');
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('nav-devices')).toBeVisible();
	await expect(page.getByTestId('toggle-light.lab_lights')).toBeVisible();

	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
	expect(overflow).toBeLessThanOrEqual(1);

	// html { overflow-x: hidden } would hide a genuine overflow from the check
	// above, so the header cluster is measured directly: nothing may be clipped
	// off the right edge.
	for (const id of ['link-status', 'palette-open', 'filter']) {
		const box = (await page.getByTestId(id).boundingBox())!;
		expect(box.x + box.width, id).toBeLessThanOrEqual(391);
		expect(box.x, id).toBeGreaterThanOrEqual(-1);
	}

	// The palette has to fit too — it is the phone's main way around.
	await page.getByTestId('palette-open').click();
	const box = (await page.getByTestId('palette').boundingBox())!;
	expect(box.width).toBeLessThanOrEqual(390);
	expect(box.x).toBeGreaterThanOrEqual(0);
	expect(box.x + box.width).toBeLessThanOrEqual(390);
	await page.keyboard.press('Escape');
});

test('keyboard focus is visible, and icon-only controls are labelled', async ({ page }) => {
	await page.goto('/devices');
	await expect(page.getByTestId('link-status')).toHaveAttribute('data-status', 'connected', {
		timeout: 15_000
	});

	// The first tab stop is the skip link, and it is drawn.
	await page.keyboard.press('Tab');
	const focus = await page.evaluate(() => {
		const el = document.activeElement as HTMLElement | null;
		if (!el) return null;
		const s = getComputedStyle(el);
		return {
			cls: el.className,
			text: el.textContent?.trim(),
			outlineWidth: parseFloat(s.outlineWidth),
			outlineStyle: s.outlineStyle
		};
	});
	expect(focus?.cls).toContain('jv-skip');
	expect(focus?.text).toBe('Skip to content');
	// A focus ring that renders as 0px or `none` is not a focus ring.
	expect(focus!.outlineWidth).toBeGreaterThanOrEqual(2);
	expect(focus!.outlineStyle).not.toBe('none');

	// Buttons whose label is a glyph still say what they do.
	await expect(page.getByTestId('prev-media_player.speaker')).toHaveAttribute(
		'aria-label',
		/previous track/i
	);
	await expect(page.getByTestId('next-media_player.speaker')).toHaveAttribute(
		'aria-label',
		/next track/i
	);
	await expect(page.getByTestId('toggle-light.lab_lights')).toHaveAttribute(
		'aria-label',
		/turn on lab lights/i
	);
});

test('the console header stays put when the page scrolls', async ({ page }) => {
	await page.setViewportSize({ width: 1280, height: 480 });
	await page.goto('/devices');
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible({ timeout: 15_000 });

	await page.evaluate(() => window.scrollTo(0, 900));
	await page.waitForTimeout(200);
	expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(200);

	// `overflow-x: hidden` on the root is one careless line away from turning the
	// document into a scroll container and quietly breaking `position: sticky`.
	const nav = (await page.getByTestId('nav-devices').boundingBox())!;
	expect(nav.y).toBeLessThan(80);
	const badge = (await page.getByTestId('link-status').boundingBox())!;
	expect(badge.y).toBeLessThan(80);
});
