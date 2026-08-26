import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { SCREENS } from '../src/lib/screens';

/**
 * Simpler menus everywhere (M55).
 *
 * The menu inventory in docs/UI_MIGRATION.md §4 is the claim; this file holds
 * every leaf screen to it against the mock backend, so "simple" is measured
 * rather than felt:
 *
 *   - at most one primary (filled) control at rest;
 *   - no two visible controls outside rows with the same name — two ways to
 *     the same thing is the thing §4 forbids;
 *   - no list row shows more controls at rest than the inventory allows;
 *   - exactly the declared number of search boxes;
 *   - on the tools page the one search empties every fold on a nonsense
 *     query and finds a built-in by name.
 *
 * The table is parsed from the document, not copied here: the inventory a
 * person reads and the one the test enforces are one table.
 */

type Row = { screen: string; route: string; rows: string; cap: number | null; primary: string; search: number };

function inventory(): Row[] {
	const doc = readFileSync(new URL('../../docs/UI_MIGRATION.md', import.meta.url), 'utf8');
	const start = doc.indexOf('### The menu inventory');
	if (start < 0) throw new Error('docs/UI_MIGRATION.md has no menu inventory');
	const rest = doc.slice(start + 10);
	const end = rest.search(/\n###? /);
	const block = end < 0 ? rest : rest.slice(0, end);
	return block
		.split('\n')
		.filter((line) => line.startsWith('| ') && line.includes('| `/'))
		.map((line) => {
			const cells = line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
			const strip = (c: string) => c.replace(/`/g, '');
			return {
				screen: cells[0],
				route: strip(cells[1]),
				rows: strip(cells[2]) === '—' ? '' : strip(cells[2]),
				cap: cells[3] === '—' ? null : Number(cells[3]),
				primary: strip(cells[4]) === '—' ? '' : strip(cells[4]),
				search: Number(cells[5])
			};
		});
}

const ROWS = inventory();
// A leaf draws its own rows: every screen except the voice screen, a detail
// page and a destination that only holds sections. Counting slashes was a
// proxy for that, and broke the day a destination had no sections
// (/dashboards, M62). The M55 gate applies the same definition.
const HOLDERS = new Set(SCREENS.map((s) => s.within).filter(Boolean));
const LEAVES = SCREENS.filter((s) => !s.path.includes('[') && s.path !== '/' && !HOLDERS.has(s.path));

const boot = async (page: Page) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
};

/** The controls a person can press: buttons, links, switches. */
const CONTROL = 'button, a[href], [role="button"], [role="switch"], input[type="checkbox"], summary';

async function visibleControls(page: Page, scope: string) {
	return page.locator(`${scope} :is(${CONTROL})`).filter({ visible: true });
}

async function nameOf(handle: import('@playwright/test').Locator): Promise<string> {
	const aria = await handle.getAttribute('aria-label');
	if (aria) return aria.trim();
	const title = await handle.getAttribute('title');
	const text = (await handle.innerText()).replace(/\s+/g, ' ').trim();
	return text || (title ?? '').trim();
}

async function open(page: Page, route: string) {
	await boot(page);
	await page.goto(route);
	const screen = SCREENS.find((s) => s.path === route);
	if (screen?.probe) await page.getByTestId(screen.probe).first().waitFor({ timeout: 15_000 });
}

/** A word to the mock over its own socket (the same hook the other specs use). */
async function tell(page: Page, payload: Record<string, unknown>) {
	await page.evaluate(
		(frame) =>
			new Promise((resolve) => {
				const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
				ws.onopen = () => ws.send(JSON.stringify({ id: 91, ...frame }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		payload
	);
}

/** Screens whose rows exist only once something has run: seed them. */
const SEED: Record<string, Record<string, unknown>> = {
	'/work/tasks': { type: 'jarvis/test/task_run' }
};

/** The section's first fetch has landed when its rows (or its primary) are drawn. */
async function settled(page: Page, row: Row): Promise<boolean> {
	if (SEED[row.route]) await tell(page, SEED[row.route]);
	let rows = true;
	if (row.cap !== null) {
		// A screen that starts empty in the mock (a job list with no job yet)
		// is measured on its other rules; the cap is measured when rows exist.
		rows = await page
			.locator('main [data-jv-row]')
			.first()
			.waitFor({ state: 'visible', timeout: 8_000 })
			.then(() => true, () => false);
	}
	if (row.primary) await page.getByTestId(row.primary).waitFor({ state: 'visible', timeout: 15_000 });
	// One more frame for the stagger to finish.
	await page.waitForTimeout(250);
	return rows;
}

test('the inventory names every leaf screen, and only those', () => {
	const listed = ROWS.map((r) => r.route).sort();
	const leaves = LEAVES.map((s) => s.path).sort();
	expect(listed.filter((r) => r !== '/')).toEqual(leaves);
});

for (const row of ROWS) {
	test(`${row.screen} holds to the inventory`, async ({ page }) => {
		await open(page, row.route);
		const hasRows = await settled(page, row);
		const main = 'main';

		// One primary at rest.
		const primaries = page.locator(`${main} .btn.primary`).filter({ visible: true });
		// The failure names the controls: on CI (01bfb30) four screens showed two
		// with no web file changed, and a count alone said nothing about which.
		const primaryNames = await primaries.evaluateAll((els) =>
			els.map((el) => el.getAttribute('data-testid') || (el.textContent || '').trim().slice(0, 40))
		);
		expect(primaryNames.length, `primary controls at rest: ${JSON.stringify(primaryNames)}`).toBeLessThanOrEqual(1);
		if (row.primary) {
			await expect(page.getByTestId(row.primary)).toBeVisible();
			expect(await page.getByTestId(row.primary).evaluate((el) => el.classList.contains('primary'))).toBe(true);
		}

		// No two ways to the same thing outside the rows.
		const outside = page
			.locator(`${main} :is(${CONTROL})`)
			.filter({ visible: true })
			.filter({ hasNot: page.locator('[data-jv-row] *') });
		const seen = new Map<string, number>();
		const n = await outside.count();
		for (let i = 0; i < n; i++) {
			const el = outside.nth(i);
			// A control inside a row belongs to the row; the locator above cannot
			// express "not a descendant of", so it is filtered here.
			if (await el.evaluate((node) => !!node.closest('[data-jv-row]'))) continue;
			const name = await nameOf(el);
			if (!name) continue;
			seen.set(name, (seen.get(name) ?? 0) + 1);
		}
		const duplicates = [...seen.entries()].filter(([, count]) => count > 1).map(([name]) => name);
		expect(duplicates, 'two visible controls with the same name outside rows').toEqual([]);

		// Rows, and what they show at rest.
		const rows = page.locator(`${main} [data-jv-row]`).filter({ visible: true });
		const rowCount = await rows.count();
		if (row.cap === null) {
			expect(rowCount, 'a screen the inventory says has no rows').toBe(0);
		} else if (!hasRows) {
			test.info().annotations.push({ type: 'note', description: `${row.route}: no rows in the mock at rest; the cap was not measured` });
		} else {
			expect(rowCount, `rows on ${row.route}`).toBeGreaterThan(0);
			if (row.rows) {
				const ids = await rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-testid') ?? ''));
				expect(ids.some((id) => id.startsWith(row.rows)), `a row whose testid starts with ${row.rows}: ${ids.slice(0, 5)}`).toBe(true);
			}
			for (let i = 0; i < Math.min(rowCount, 12); i++) {
				const one = rows.nth(i);
				const controls = one.locator(`:is(${CONTROL})`).filter({ visible: true });
				const count = await controls.count();
				expect(count, `controls at rest on row ${await one.getAttribute('data-testid')}`).toBeLessThanOrEqual(row.cap);
			}
		}

		// Search boxes.
		const searches = page.locator(`${main} [data-jv-filter]`).filter({ visible: true });
		expect(await searches.count(), 'search boxes').toBe(row.search);
	});
}

test('TOOLS: the one search filters every fold, and finds a built-in by name', async ({ page }) => {
	await open(page, '/settings/tools');
	await settled(page, ROWS.find((r) => r.route === '/settings/tools')!);
	const search = page.locator('main [data-jv-filter]');
	await expect(search).toHaveCount(1);
	// Open every fold so its rows count.
	for (const fold of await page.locator('main details.fold').all()) {
		if (!(await fold.evaluate((d) => (d as HTMLDetailsElement).open))) await fold.locator('summary').click();
	}
	const rows = page.locator('main [data-jv-row]').filter({ visible: true });
	expect(await rows.count()).toBeGreaterThan(3);
	await search.fill('zzzz-nothing-is-called-this');
	await expect(rows).toHaveCount(0);
	await search.fill('get_state');
	await expect(page.getByTestId('tool-get_state')).toBeVisible();
	expect(await rows.count()).toBeGreaterThanOrEqual(1);
	await search.fill('');
	expect(await rows.count()).toBeGreaterThan(3);
});

test('DASHBOARDS: on an owned dashboard, + Widget is the one primary and the one way into the layout editor', async ({ page }) => {
	await open(page, '/house/dashboards');
	await page.getByTestId('dashboard-picker').selectOption('mine');
	const add = page.getByTestId('dashboard-add');
	await expect(add).toBeVisible({ timeout: 10_000 });
	expect(await page.locator('main .btn.primary').filter({ visible: true }).count()).toBe(1);
	await expect(page.getByTestId('dashboard-edit')).toHaveCount(0);
	await add.click();
	await expect(page.getByTestId('dashboard-edit')).toHaveText(/done/i);
	await expect(add).toHaveCount(0);
	expect(await page.locator('main .btn.primary').filter({ visible: true }).count()).toBeLessThanOrEqual(1);
	await page.getByTestId('dashboard-edit').click();
	await expect(add).toBeVisible();
});
