import { test, expect, type Page } from '@playwright/test';

/**
 * Changing an entity's id from the console.
 *
 * This answered "renaming an entity_id is not supported yet" until now, and
 * the console had no box for it. The interesting part is not the box — it is
 * that an `entity_id` is a KEY: the registry files the entity under it, the
 * state machine files its state under it, and every automation names its
 * targets with it. A rename that moves one of the three leaves an entity that
 * exists twice and works neither way.
 *
 * So what these check is that the id moves and the row still works afterwards,
 * and that the two refusals reach the person BEFORE they press SAVE.
 */

const OPEN = { timeout: 15_000 };

/**
 * Put the registry and the states back.
 *
 * One mock process serves the whole file, and a rename mutates both — so
 * without this the first test in the file silently decides what the rest see.
 */
async function reset(page: Page): Promise<void> {
	await page.evaluate(
		() =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () => ws.send(JSON.stringify({ id: 71, type: 'jarvis/test/registry_reset' }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			})
	);
}

test.beforeEach(async ({ page }) => {
	// The socket needs a page to live in, so this is a goto and then a reset
	// and then the real navigation.
	await page.goto('/devices');
	await reset(page);
});

test('an entity keeps working under its new id', async ({ page }) => {
	await page.goto('/devices');
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible(OPEN);

	// `textContent`, not `innerText`: the pill is uppercased in CSS, and
	// comparing a rendered "OFF" against the row's "off" would be measuring
	// the stylesheet.
	const before = (
		await page.getByTestId('state-light.lab_lights').textContent()
	)?.trim();
	await page.getByTestId('manage-light.lab_lights').click();
	const id = page.getByTestId('id-light.lab_lights');
	await expect(id).toHaveValue('light.lab_lights');

	await id.fill('light.workshop');
	// The consequence is named before the button is pressed.
	await expect(page.getByTestId('summary-light.lab_lights')).toContainText('light.workshop');
	await page.getByTestId('save-light.lab_lights').click();

	// The row is there under the new id, still showing the state it had — not
	// `unknown`, which is what a rename that recreated the entity would leave.
	const moved = page.getByTestId('entity-light.workshop');
	await expect(moved).toBeVisible(OPEN);
	await expect(page.getByTestId('state-light.workshop')).toHaveText(before ?? '');
	await expect(page.getByTestId('entity-light.lab_lights')).toHaveCount(0);
});

test('an id that is taken is refused before it is sent', async ({ page }) => {
	await page.goto('/devices');
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible(OPEN);
	await page.getByTestId('manage-light.lab_lights').click();

	// The console cannot know what is taken, so this one does reach the server
	// — and comes back as a message rather than a silent failure.
	await page.getByTestId('id-light.lab_lights').fill('light.hall_lamp');
	await page.getByTestId('save-light.lab_lights').click();
	await expect(page.getByTestId('toast').first()).toContainText('already exists');
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible();
});

test('a change of domain is refused with the reason, not just a no', async ({ page }) => {
	await page.goto('/devices');
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible(OPEN);
	await page.getByTestId('manage-light.lab_lights').click();

	await page.getByTestId('id-light.lab_lights').fill('switch.lab_lights');
	const problem = page.getByTestId('id-problem-light.lab_lights');
	await expect(problem).toContainText('light');
	await expect(problem).toContainText('services');
	// Nothing to send: the id is not part of `pending` while it is invalid.
	await expect(page.getByTestId('save-light.lab_lights')).toBeDisabled();
});

test('a malformed id says what the shape is', async ({ page }) => {
	await page.goto('/devices');
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible(OPEN);
	await page.getByTestId('manage-light.lab_lights').click();

	await page.getByTestId('id-light.lab_lights').fill('Lab Lights');
	await expect(page.getByTestId('id-problem-light.lab_lights')).toContainText(
		'light.kitchen'
	);
});
