import { test, expect } from '@playwright/test';

/**
 * A row of controls is one row.
 *
 * `.row-wrap` — the element that pairs an entity's controls with the MANAGE
 * button that opens its editor — had no styling at all. A bare `div` is a block,
 * and the row inside it lays itself out flex and full-width, so the button went
 * UNDERNEATH. Every entity was two lines tall with a chunky ghost button on the
 * second, which put a column of nine identical buttons down the left of the page
 * and doubled its height: at 1280x900 the Devices page showed two and a half
 * groups where it now shows five.
 *
 * Nothing caught it. The button was present, labelled, clickable and reachable
 * by keyboard; every assertion anyone would write about it passed. The only
 * thing wrong was where it was, which is exactly the class of defect that
 * survives a test suite and greets the user on the first screen they open.
 *
 * So this asserts geometry, which is the property that was wrong: on a desktop
 * viewport the button shares a line with the row it belongs to. It deliberately
 * does NOT assert that at phone width — wrapping there is correct, and pinning
 * it would forbid the responsive behaviour the rest of the suite requires.
 */
test('an entity and its MANAGE button share a line on a desktop viewport', async ({ page }) => {
	await page.setViewportSize({ width: 1280, height: 900 });
	await page.goto('/devices');

	const manage = page.getByTestId('manage-light.lab_lights');
	await expect(manage).toBeVisible();
	const row = page.getByTestId('entity-light.lab_lights');
	await expect(row).toBeVisible();

	// Read both boxes together, or nothing.
	//
	// This used to be a straight-line read, and it went red on a commit that
	// touched no web code at all, with `the MANAGE button has no box` — a null
	// from `boundingBox()` one statement after `toBeVisible()` had passed. That
	// is not a layout failure, it is a re-render: the page keeps taking entity
	// states from the mock backend after first paint, and a row that Svelte
	// replaces between the two calls leaves the already-resolved handle pointing
	// at a detached node, which has no box. A single sample can also catch the
	// row mid-reflow, before web fonts have settled the row height this
	// assertion measures against.
	//
	// So: sample until the geometry holds, and let the poll's own timeout be
	// the failure. This does NOT weaken the assertion. A button that is
	// genuinely stacked underneath its row is stacked on every sample — its
	// centre stays a full row away and its x stays at the left margin — so the
	// condition below is never satisfied and the poll fails on timeout. What it
	// stops being able to do is fail on the one sample where the answer was
	// 'ask again'.
	const geometry = async () => {
		const button = await manage.boundingBox();
		const controls = await row.boundingBox();
		if (!button || !controls) return null;
		return {
			// Centres within half a row of each other. A tolerance rather than
			// an equality: the button is shorter than the row it sits beside,
			// and flex centring is not pixel-identical across platforms.
			offset: Math.abs(button.y + button.height / 2 - (controls.y + controls.height / 2)),
			allowed: controls.height / 2 + 4,
			// ...and it is to the RIGHT of the controls, not stacked under them.
			buttonX: button.x,
			midX: controls.x + controls.width / 2
		};
	};

	await expect
		.poll(
			async () => {
				const g = await geometry();
				return g ? g.offset < g.allowed && g.buttonX > g.midX : false;
			},
			{
				timeout: 10_000,
				message:
					'the MANAGE button never shared a line with the entity it manages — ' +
					'it is stacked underneath rather than at the end of the row'
			}
		)
		.toBe(true);

	// Re-read once settled, so anything that regresses downstream of here
	// reports real numbers rather than a bare `expected true, got false`.
	const settled = await geometry();
	expect(settled, 'the row or its MANAGE button has no box').not.toBeNull();
	expect(
		settled!.offset,
		'the MANAGE button is not on the same line as the entity it manages'
	).toBeLessThan(settled!.allowed);
	expect(settled!.buttonX, 'the MANAGE button is not at the end of the row').toBeGreaterThan(
		settled!.midX
	);
});

test('opening an editor does not draw a separator between it and its own row', async ({ page }) => {
	await page.setViewportSize({ width: 1280, height: 900 });
	await page.goto('/devices');

	const wrap = page.locator('.row-wrap', { has: page.getByTestId('manage-light.lab_lights') });
	await page.getByTestId('manage-light.lab_lights').click();
	await expect(page.getByTestId('editor-light.lab_lights')).toBeVisible();

	// The editor drops out of the row above it. A dashed rule between a control
	// and the panel it just opened reads as a boundary between two unrelated
	// things, which is the opposite of what just happened.
	const border = await wrap.evaluate((el) => getComputedStyle(el).borderBottomStyle);
	expect(border, 'the open row is still separated from its own editor').toBe('none');
});
