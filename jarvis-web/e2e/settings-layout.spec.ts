import { test, expect, type Page } from '@playwright/test';

/**
 * M107 — settings rows line up. Every settings row is one SettingRow on one
 * grid, so the value column starts at the same left edge in every panel on
 * every tab, and every control fills its cell. Measured, not asserted from
 * CSS: the review pictures of 27 Aug 2026 had the value column at 617 px,
 * 582 px and 611 px in three panels of the same page, and four widths of
 * control in one panel.
 */

const TABS = ['assistant', 'voice', 'house', 'console'];

async function valueEdges(page: Page): Promise<{ left: number; width: number; row: string }[]> {
	return page.evaluate(() => {
		const out: { left: number; width: number; row: string }[] = [];
		for (const row of Array.from(document.querySelectorAll('[data-jv-row]'))) {
			const value = row.querySelector('[data-jv-value]') as HTMLElement | null;
			if (!value || value.offsetParent === null) continue;
			const r = value.getBoundingClientRect();
			if (r.width === 0) continue;
			out.push({ left: Math.round(r.left), width: Math.round(r.width), row: (row as HTMLElement).dataset.testid ?? row.className });
		}
		return out;
	});
}

test.use({ viewport: { width: 1440, height: 900 } });

test('every value cell starts at one left edge, on every tab', async ({ page }) => {
	const edges = new Map<string, number[]>();
	for (const tab of TABS) {
		await page.goto(`/settings/${tab}`);
		await page.waitForTimeout(600);
		const cells = await valueEdges(page);
		expect(cells.length, `${tab} has settings rows`).toBeGreaterThan(0);
		edges.set(tab, cells.map((c) => c.left));
		test.info().annotations.push({ type: tab, description: cells.map((c) => `${c.row}:${c.left}`).join(' ') });
	}
	const all = Array.from(edges.values()).flat();
	const min = Math.min(...all), max = Math.max(...all);
	expect(max - min, `value-column left edges across tabs: ${test.info().annotations.map((a) => `${a.type}: ${a.description}`).join(' | ')}`).toBeLessThanOrEqual(2);
});

test('within a panel, controls share a width — a select is as wide as an input', async ({ page }) => {
	for (const tab of ['voice', 'assistant']) {
		await page.goto(`/settings/${tab}`);
		await page.waitForTimeout(600);
		const widths = await page.evaluate(() => {
			const out: number[] = [];
			for (const cell of Array.from(document.querySelectorAll('[data-jv-row] [data-jv-value]'))) {
				const control = cell.querySelector('select, input:not([type=checkbox]), textarea') as HTMLElement | null;
				if (control && control.offsetParent !== null) out.push(Math.round(control.getBoundingClientRect().width));
			}
			return out;
		});
		if (widths.length < 2) continue;
		const widest = Math.max(...widths), narrowest = Math.min(...widths);
		expect(widest - narrowest, `${tab}: control widths ${widths.join(', ')}`).toBeLessThanOrEqual(48);
	}
});
