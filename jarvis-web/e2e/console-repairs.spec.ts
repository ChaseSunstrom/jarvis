import { test, expect } from '@playwright/test';

/**
 * Things the console did wrong to the person using it.
 *
 * None of these were crashes and none of them showed up in a suite that
 * asserted the right elements existed: a page that cannot recover from a
 * dropped socket still renders, an editor that discards what you typed still
 * renders, a section that says "everything has an area" before it has been told
 * about any areas renders best of all.
 */

test('a dropped socket can be reconnected without reloading the tab', async ({ page }) => {
	// A page's socket deliberately does not reattach on its own — a reconnected
	// socket has none of the page's subscriptions, so silently reattaching would
	// leave stale rows looking live. That was right, and it left the user with a
	// dead page, a line of grey text saying "link closed", and no button.
	const sockets: { close: () => void }[] = [];
	await page.routeWebSocket(/\/ws$/, (ws) => {
		const server = ws.connectToServer();
		ws.onMessage((message) => server.send(message));
		server.onMessage((message) => ws.send(message));
		sockets.push(ws);
	});

	await page.goto('/devices');
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('link-dropped')).toHaveCount(0);

	for (const socket of sockets.splice(0)) socket.close();

	const dropped = page.getByTestId('link-dropped');
	await expect(dropped).toBeVisible({ timeout: 10_000 });
	await expect(dropped).toContainText('will not come back on its own');

	await page.getByTestId('reconnect').click();
	await expect(dropped).toHaveCount(0, { timeout: 15_000 });

	// The banner going is NOT the page being live, and treating it as such is
	// what made this test flake. `down` is `status === 'closed' || 'error'`, so
	// the notice unmounts the instant the replacement socket starts dialling —
	// while `connect()` has still to load the states, load the companions and
	// re-subscribe to state_changed. Toggling in that window sends a service
	// call whose event arrives before there is a subscription to hear it, so the
	// pill never moves and the assertion below times out on a page that had
	// reconnected perfectly well.
	//
	// `redialling` spans all three steps and is only cleared in `connect()`'s
	// `finally`, which is the first moment the rows can be trusted.
	await expect(page.getByTestId('devices-lede')).toHaveAttribute('data-redialling', 'false', {
		timeout: 15_000
	});

	// Not merely reconnected: reloaded and re-subscribed, so what is on screen is
	// live again. A toggle round-trips over the new socket.
	const pill = page.getByTestId('state-light.lab_lights');
	const before = (await pill.textContent())?.trim();
	await page.getByTestId('toggle-light.lab_lights').click();
	await expect(pill).not.toHaveText(before ?? '', { timeout: 10_000 });
	await page.getByTestId('toggle-light.lab_lights').click();
	await expect(pill).toHaveText(before ?? '', { timeout: 10_000 });

	// Every management page keeps its own socket, so every one of them needed the
	// same way back. Settings is the one somebody is most likely to be sitting on
	// when a backend restarts underneath them.
	await page.goto('/settings');
	await expect(page.getByTestId('setting-llm.timeout')).toBeVisible({ timeout: 15_000 });
	for (const socket of sockets.splice(0)) socket.close();
	await expect(page.getByTestId('link-dropped')).toBeVisible({ timeout: 10_000 });
	await page.getByTestId('reconnect').click();
	await expect(page.getByTestId('link-dropped')).toHaveCount(0, { timeout: 15_000 });
});

test('the tool runner never points at a tool that is not on its list', async ({ page }) => {
	// The picker was filled from the filtered catalogue while the selection was
	// separate state, so filtering away the selected tool left an empty box with
	// RUN still lit — and `selectedTool` was looked up in `tools` rather than in
	// the catalogue, so a console-created tool never showed its description.
	await page.goto('/tools');
	const select = page.getByTestId('tool-select');
	await expect(select).toBeVisible({ timeout: 15_000 });

	const options = await select.locator('option').allTextContents();
	expect(options.length).toBeGreaterThan(1);
	await select.selectOption(options[options.length - 1]);
	const chosen = await select.inputValue();

	// A filter that excludes it must not blank the control it is bound to.
	await page.getByTestId('tool-filter').fill('zzzz no such tool');
	await expect(page.getByTestId('empty')).toBeVisible();
	await expect(select).toHaveValue(chosen);
	await expect(select.locator('option')).toHaveCount(1);
});

test('the areas page does not claim everything has an area before it knows anything', async ({
	page
}) => {
	// The "Unassigned" section sat outside the loading guard, so between the
	// page rendering and the registry answering it stated — in the same words it
	// uses when it is true — the exact opposite of the truth on a fresh install.
	//
	// The socket here is accepted and then never answered, which holds the page
	// in the state it used to lie in for as long as the assertions need.
	await page.routeWebSocket(/\/ws$/, () => {
		/* connected to nothing: every command goes unanswered */
	});

	await page.goto('/areas');
	const unassigned = page.getByTestId('area-unassigned');
	await expect(unassigned).toBeVisible();
	await expect(unassigned.getByTestId('skeleton')).toBeVisible();
	await expect(page.getByTestId('all-assigned')).toHaveCount(0);
});

test('an editor with unsaved edits is not thrown away by the next click', async ({ page }) => {
	await page.goto('/devices');
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible({ timeout: 15_000 });

	await page.getByTestId('manage-light.lab_lights').click();
	const name = page.getByTestId('name-light.lab_lights');
	await expect(name).toBeVisible();
	await name.fill('Workshop lamp');

	// Opening somebody else's editor used to reassign `form` outright.
	await page.getByTestId('manage-switch.desk_fan').click();
	await expect(page.getByTestId('editor-light.lab_lights')).toBeVisible();
	await expect(name).toHaveValue('Workshop lamp');
	await expect(page.getByTestId('editor-switch.desk_fan')).toHaveCount(0);
	await expect(page.getByTestId('toast').first()).toContainText('Unsaved changes');

	// The second press means it — the same two-press rule the delete buttons use.
	await page.getByTestId('manage-switch.desk_fan').click();
	await expect(page.getByTestId('editor-switch.desk_fan')).toBeVisible();
	await expect(page.getByTestId('editor-light.lab_lights')).toHaveCount(0);

	// A clean editor still closes on the first press, because there is nothing
	// to lose — a guard that asks every time is a guard nobody reads.
	await page.getByTestId('manage-switch.desk_fan').click();
	await expect(page.getByTestId('editor-switch.desk_fan')).toHaveCount(0);
});

test('the palette only announces a listbox while it has one', async ({ page }) => {
	await page.goto('/devices');
	await page.getByTestId('palette-open').click();
	const input = page.getByTestId('palette-input');
	await expect(input).toHaveAttribute('aria-expanded', 'true');
	await expect(input).toHaveAttribute('aria-controls', 'jv-palette-list');

	await input.fill('zzzz no such entity zzzz');
	await expect(page.getByTestId('palette-none')).toBeVisible();
	// The `<ul>` is gone, so saying it is there — and pointing an `aria-controls`
	// at an id that is not in the document — is a combobox a screen reader
	// cannot navigate and cannot explain.
	await expect(input).toHaveAttribute('aria-expanded', 'false');
	expect(await input.getAttribute('aria-controls')).toBeNull();
});

test('a slider label keeps the chrome font and the dim colour it asks for', async ({ page }) => {
	// EntityRow reached for `--chrome` and `--dim`, which are declared inside
	// `.hud` on the HUD page. This row only ever renders inside `.console`, so
	// both lookups fell through and every BRI / VOL / POS label silently
	// inherited the body face at full text colour.
	await page.goto('/devices');
	await expect(page.locator('.slabel').first()).toBeVisible({ timeout: 15_000 });

	// Queried and measured inside one synchronous evaluate. A handle taken on one
	// tick and read on the next can be pointing at a node the list has already
	// replaced, and `getComputedStyle` of a detached node is every property
	// empty — which reads as "the font is wrong" rather than "ask again".
	const styles = await page.evaluate(() => {
		const label = document.querySelector('.slabel');
		if (!label) return null;
		const probe = document.createElement('span');
		probe.style.color = 'var(--jv-text-dim)';
		document.body.append(probe);
		const dim = getComputedStyle(probe).color;
		probe.remove();
		const computed = getComputedStyle(label);
		return { font: computed.fontFamily.toLowerCase(), colour: computed.color, dim };
	});

	expect(styles, 'no slider label on the page').not.toBeNull();
	expect(styles!.font).toContain('mono');
	expect(styles!.colour).toBe(styles!.dim);
});

test('text is readable by default, and the reader can make it larger', async ({ page }) => {
	await page.goto('/devices');
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible({ timeout: 15_000 });

	// The chrome used to bottom out at 0.55rem — under nine pixels — and this is
	// the size most of the console's text is actually drawn at.
	const eid = page.locator('.eid').first();
	const px = (locator: typeof eid) =>
		locator.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
	expect(await px(eid)).toBeGreaterThanOrEqual(11);

	await page.goto('/settings');
	const root = () =>
		page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize));
	const standard = await root();
	await page.getByTestId('text-size-large').click();
	const large = await root();
	expect(large).toBeGreaterThan(standard);

	// It is a preference, so it survives a reload...
	await page.reload();
	await expect.poll(root).toBe(large);
	// ...and it is the whole interface, not this page: the HUD is sized in the
	// same rem the console is.
	await page.goto('/');
	await expect(page.getByTestId('mic')).toBeVisible({ timeout: 10_000 });
	await expect.poll(root).toBe(large);

	// Put it back, so the rest of the run sees the size it expects.
	await page.goto('/settings');
	await page.getByTestId('text-size-standard').click();
	await expect.poll(root).toBe(standard);
});

test('a number setting is saved as a number', async ({ page }) => {
	// The page said in a comment that it coerced the form string to the
	// setting's type, and returned the string for numbers — so every numeric
	// setting the console has ever saved was saved as text.
	// Watching before the page opens its socket, or the frames go past unseen.
	const sent: string[] = [];
	page.on('websocket', (ws) =>
		ws.on('framesent', (frame) => {
			if (typeof frame.payload === 'string') sent.push(frame.payload);
		})
	);

	await page.goto('/settings');
	const field = page.getByTestId('input-llm.timeout');
	await expect(field).toBeVisible({ timeout: 15_000 });

	await field.fill('42');
	await page.getByTestId('save-llm.timeout').click();
	await expect
		.poll(() =>
			sent
				.map((frame) => {
					try {
						return JSON.parse(frame);
					} catch {
						return null;
					}
				})
				.find((msg) => msg?.type === 'config/settings/set')
		)
		.toMatchObject({ key: 'llm.timeout', value: 42 });
});
