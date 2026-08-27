import { test, expect, type Page } from '@playwright/test';

/**
 * The house is editable (M69): an entity can be taken out of it, from the
 * Devices screen and by the assistant after a spoken yes, and every surface
 * follows the one `state_changed` a removal fires.
 *
 * The console could edit an entity's name, area and exposure and never remove
 * one; the only way to make a thing go away was to edit `.storage/` by hand.
 * The mock mirrors jarvis-core's delete path: the registry entry goes, the
 * state goes with a `state_changed` carrying no `new_state`, and the registry
 * event says `remove`.
 */

const OPEN = { timeout: 15_000 };

async function send(page: Page, body: Record<string, unknown>): Promise<unknown> {
	return page.evaluate(
		(frame) =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () => ws.send(JSON.stringify({ id: 81, ...frame }));
				ws.onmessage = (ev) => {
					ws.close();
					resolve(JSON.parse(ev.data as string));
				};
			}),
		body
	);
}

test.beforeEach(async ({ page }) => {
	// One mock process serves the whole file and a removal is for good, so
	// every test starts from the seed house.
	await page.goto('/devices');
	await send(page, { type: 'jarvis/test/registry_reset' });
});

test('REMOVE takes an entity out of the house, on the second press', async ({ page }) => {
	await page.goto('/devices');
	await expect(page.getByTestId('entity-switch.desk_fan')).toBeVisible(OPEN);
	await page.getByTestId('manage-switch.desk_fan').click();
	const remove = page.getByTestId('remove-switch.desk_fan');
	await expect(remove).toHaveText('REMOVE');

	// The first press arms: nothing has gone.
	await remove.click();
	await expect(remove).toHaveText('REMOVE — SURE?');
	await expect(page.getByTestId('entity-switch.desk_fan')).toBeVisible();

	// The second removes. The row leaves on the state_changed, not on the reply.
	await remove.click();
	await expect(page.getByTestId('entity-switch.desk_fan')).toHaveCount(0, OPEN);
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible();

	const entries = (await send(page, { type: 'config/entity_registry/list' })) as {
		result: { entity_id: string }[];
	};
	expect(entries.result.map((e) => e.entity_id)).not.toContain('switch.desk_fan');
	await expect(page.getByTestId('error')).toHaveCount(0);
});

test('a removal made elsewhere — the assistant, another tab — drops the row live', async ({
	page
}) => {
	await page.goto('/devices');
	await expect(page.getByTestId('entity-light.hall_lamp')).toBeVisible(OPEN);

	const reply = (await send(page, {
		type: 'config/entity_registry/remove',
		entity_id: 'light.hall_lamp'
	})) as { result: Record<string, unknown> };
	expect(reply.result).toEqual({
		entity_id: 'light.hall_lamp',
		removed: true,
		had_state: true,
		had_registry_entry: true
	});

	await expect(page.getByTestId('entity-light.hall_lamp')).toHaveCount(0, OPEN);
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible();

	// Twice is not found, not a silent success.
	const again = (await send(page, {
		type: 'config/entity_registry/remove',
		entity_id: 'light.hall_lamp'
	})) as { error: { code: string } };
	expect(again.error.code).toBe('not_found');
});

test('a dashboard tile whose entity was removed says so, rather than drawing stale state', async ({
	page
}) => {
	await page.goto('/dashboards');
	await expect(page.getByTestId('tile-state-light.hall_lamp')).toHaveText(/on/, OPEN);

	await send(page, { type: 'config/entity_registry/remove', entity_id: 'light.hall_lamp' });

	const why = page.getByTestId('tile-why');
	await expect(why).toBeVisible(OPEN);
	await expect(why).toContainText('light.hall_lamp');
	await expect(why).toContainText('was removed from this Jarvis');
	await expect(why).toHaveAttribute('data-removed', 'true');
	await expect(page.getByTestId('tile-state-light.hall_lamp')).toHaveCount(0);
	await expect(page.getByTestId('toggle-light.hall_lamp')).toHaveCount(0);
});
