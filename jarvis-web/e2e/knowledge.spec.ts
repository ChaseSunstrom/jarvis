import { test, expect, type Page } from '@playwright/test';

/**
 * KNOWLEDGE as one graph (M50).
 *
 * The operator asked for "a literal nice looking graph that animates when
 * Jarvis uses it". What can be proved: the graph has one point per note and
 * per remembered fact and an edge for a `[[link]]`; picking a point opens the
 * note; a turn that read a remembered fact lights it and it settles again; a
 * note tool lights the note it touched; and under reduced motion nothing
 * animates while the lit state is still reported. Whether it is nice looking
 * is what `docs/ui-review/knowledge/` is for.
 */

const gotoKnowledge = async (page: Page, path = '/knowledge/notes') => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto(path);
	await expect(page.getByTestId('knowledge-graph')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('graph')).toBeVisible({ timeout: 15_000 });
};

/** Send one test hook down a fresh socket, the way the other specs do. */
const hook = (page: Page, payload: Record<string, unknown>) =>
	page.evaluate(
		(msg) =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () => ws.send(JSON.stringify({ id: 97, ...msg }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		payload
	);

test('every note and every remembered fact is a point, and a link is an edge', async ({ page }) => {
	await gotoKnowledge(page);
	const graph = page.getByTestId('graph');
	// The mock ships four notes (the research report since M106) and two memory entries.
	await expect(graph).toHaveAttribute('data-nodes', '6');
	await expect(page.getByTestId('graph-node-note:cheap-rate-report')).toBeVisible();
	await expect(page.getByTestId('graph-node-note:boiler-serviced')).toBeVisible();
	await expect(page.getByTestId('graph-node-note:heating')).toBeVisible();
	await expect(page.getByTestId('graph-node-note:research-heat-pumps')).toBeVisible();
	await expect(page.getByTestId('graph-node-memory:mem1')).toBeVisible();
	await expect(page.getByTestId('graph-node-memory:mem2')).toBeVisible();
	// "Boiler serviced" says [[heating]]: one link edge, drawn once however
	// many times the two notes point at each other. The shared `house` tag
	// joins the spare-key fact to both, quieter.
	await expect(graph.locator('line.edge.link')).toHaveCount(1);
	await expect(graph.locator('line.edge.tag')).not.toHaveCount(0);
	await expect(page.getByTestId('knowledge-graph')).toContainText('3 notes · 2 remembered');
});

test('picking a point opens that note, and the URL says which', async ({ page }) => {
	await gotoKnowledge(page);
	// The point itself, not the group's box: the group's centre falls on the
	// label, which lets clicks through to the drawing underneath.
	await page.getByTestId('graph-node-note:boiler-serviced').locator('circle.hit').click();
	await expect(page).toHaveURL(/\/knowledge\/notes\?open=boiler-serviced$/);
	await expect(page.getByTestId('note-editor')).toBeVisible({ timeout: 10_000 });
	await expect(page.getByTestId('note-title')).toHaveText('Boiler serviced');
	// And the point is the selected one, on the map as in the list.
	await expect(page.getByTestId('graph-node-note:boiler-serviced')).toHaveAttribute('aria-pressed', 'true');
	await expect(page.getByTestId('note-row-boiler-serviced')).toHaveClass(/open/);

	// A remembered fact opens the memory section at that entry.
	await page.getByTestId('graph-node-memory:mem2').locator('circle.hit').click();
	await expect(page).toHaveURL(/\/knowledge\/memory\?entry=mem2$/);
	await expect(page.getByTestId('memory-entry-mem2')).toHaveClass(/picked/, { timeout: 10_000 });
	await expect(page.getByTestId('graph-node-memory:mem2')).toHaveAttribute('aria-pressed', 'true');
});

test('a turn that read a remembered fact lights it, and it settles again', async ({ page }) => {
	await gotoKnowledge(page);
	const graph = page.getByTestId('graph');
	await expect(graph).toHaveAttribute('data-lit', '0');

	// Raised until it lands: the layout's socket may still be subscribing.
	await expect
		.poll(
			async () => {
				await hook(page, { type: 'jarvis/test/memory_used', entries: ['mem1'] });
				return graph.getAttribute('data-lit');
			},
			{ timeout: 20_000, intervals: [300, 700, 1500] }
		)
		.toBe('1');
	await expect(page.getByTestId('graph-node-memory:mem1')).toHaveClass(/lit/);
	await expect(page.getByTestId('graph-node-memory:mem2')).not.toHaveClass(/lit/);
	// The lit window is one slow blink (--jv-dur-blink, 2.4 s); after it the
	// picture is calm again rather than lit forever.
	await expect(graph).toHaveAttribute('data-lit', '0', { timeout: 6_000 });
	await expect(page.getByTestId('graph-node-memory:mem1')).not.toHaveClass(/lit/);
});

test('a note tool lights the note it touched', async ({ page }) => {
	await gotoKnowledge(page);
	const graph = page.getByTestId('graph');
	await expect
		.poll(
			async () => {
				await hook(page, {
					type: 'jarvis/test/tool_run',
					tools: ['note_search'],
					arguments: { query: 'heat pumps' }
				});
				return graph.getAttribute('data-lit');
			},
			{ timeout: 20_000, intervals: [300, 700, 1500] }
		)
		.not.toBe('0');
	await expect(page.getByTestId('graph-node-note:research-heat-pumps')).toHaveClass(/lit/);
	await expect(page.getByTestId('graph-node-note:boiler-serviced')).not.toHaveClass(/lit/);
});

test('under reduced motion nothing on the graph animates, and lit is still reported', async ({
	page
}) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await gotoKnowledge(page);
	const graph = page.getByTestId('graph');
	await expect
		.poll(
			async () => {
				await hook(page, { type: 'jarvis/test/memory_used', entries: ['mem2'] });
				return graph.getAttribute('data-lit');
			},
			{ timeout: 20_000, intervals: [300, 700, 1500] }
		)
		.toBe('1');
	const running = await graph.evaluate(
		(el) => el.getAnimations({ subtree: true }).filter((a) => a.playState === 'running').length
	);
	expect(running, `${running} animations running on the graph under reduced motion`).toBe(0);
	await expect(page.getByTestId('graph-node-memory:mem2')).toHaveClass(/lit/);
});

test('the graph holds at a phone width, above the section', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await gotoKnowledge(page);
	const graph = await page.getByTestId('graph').boundingBox();
	const strip = await page.locator('nav[aria-label="Sections"]').boundingBox();
	expect(graph!.x + graph!.width).toBeLessThanOrEqual(390);
	expect(strip!.y).toBeGreaterThan(graph!.y + graph!.height);
});

test('no two labels print over each other', async ({ page }) => {
	// The mock's five entries include two that settle at one height a label's
	// width apart; before the labels dodged, "Boiler serviced" printed across
	// the end of "The spare key…". Every pair of label boxes must be disjoint.
	await gotoKnowledge(page);
	const labels = page.locator('[data-testid^="graph-node-"] text');
	await expect(labels).toHaveCount(5);
	const boxes = (await labels.evaluateAll((els) =>
		els.map((el) => {
			const r = el.getBoundingClientRect();
			return { x1: r.left, x2: r.right, y1: r.top, y2: r.bottom, text: el.textContent };
		})
	)).filter((b) => b.x2 > b.x1);
	expect(boxes.length).toBe(5);
	for (let i = 0; i < boxes.length; i++) {
		for (let j = i + 1; j < boxes.length; j++) {
			const a = boxes[i];
			const b = boxes[j];
			const overlap = a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1;
			expect(overlap, `"${a.text}" overlaps "${b.text}"`).toBe(false);
		}
	}
});
