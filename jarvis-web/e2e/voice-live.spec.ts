import { test, expect, type Page } from '@playwright/test';

/**
 * VOICE, alive (M52).
 *
 * The voice tab is where the operator looks. It has to show what Jarvis is
 * doing — not only the exchange, but the work: a tool as it runs, a task as
 * it steps, a sensor as it changes, a camera as it is looked at, a fact
 * remembered, a moment landing — and the knowledge graph, lighting as it is
 * used. Every row here is driven through the mock backend's test hooks,
 * which fire the same bus events the core does.
 */

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

async function gotoVoice(page: Page) {
	await page.goto('/');
	await expect(page.getByTestId('voice-screen')).toBeVisible();
	// The strip and the graph exist from the first paint; the link may still
	// be subscribing, which is why the assertions below poll.
	await expect(page.getByTestId('activity')).toBeVisible();
	await expect(page.getByTestId('voice-graph')).toBeVisible();
}

async function untilRow(page: Page, kind: string, hookPayload: Record<string, unknown>) {
	await expect
		.poll(
			async () => {
				await hook(page, hookPayload);
				return page.getByTestId(`activity-row-${kind}`).count();
			},
			{ timeout: 20_000, intervals: [300, 700, 1500] }
		)
		.toBeGreaterThan(0);
	return page.getByTestId(`activity-row-${kind}`).first();
}

test('the graph is on the voice tab, with every note and remembered fact', async ({ page }) => {
	await gotoVoice(page);
	// Every note and fact as SEEDED: a spec earlier in the same run may have
	// forgotten one, and the mock is one process for the whole suite.
	await hook(page, { type: 'jarvis/test/knowledge_reset' });
	await page.reload();
	await expect(page.getByTestId('voice-screen')).toBeVisible();
	const graph = page.getByTestId('voice-graph');
	await expect(graph).toHaveAttribute('data-nodes', '5');
});

test('a tool call shows as it runs, then stamps its result', async ({ page }) => {
	await gotoVoice(page);
	const row = await untilRow(page, 'tool', {
		type: 'jarvis/test/tool_run',
		tools: ['get_state'],
		arguments: { entity_id: 'light.hall_lamp' }
	});
	await expect(row).toContainText('get_state');
	// Finished lands 40 ms after the start in the mock; the row updates in place.
	await expect(row).toHaveAttribute('data-state', 'done', { timeout: 5_000 });
	await expect(page.getByTestId('activity')).toHaveAttribute('data-count', '1');
});

test('a task stepping is a task row that follows the task', async ({ page }) => {
	await gotoVoice(page);
	const row = await untilRow(page, 'task', {
		type: 'jarvis/test/task_run',
		title: 'Read twelve pages',
		steps: ['search', 'read', 'write up']
	});
	await expect(row).toContainText('Read twelve pages');
	await expect(row).toContainText('steps');
});

test('a sensor changing is a reading with its unit', async ({ page }) => {
	await gotoVoice(page);
	const row = await untilRow(page, 'sensor', {
		type: 'jarvis/test/sensor_change',
		entity_id: 'sensor.lab_temperature',
		value: '23.1'
	});
	await expect(row).toContainText('Lab Temperature');
	await expect(row).toContainText('23.1 °C');
});

test('looking at a camera is said under the reactor while it lasts', async ({ page }) => {
	await gotoVoice(page);
	const row = await untilRow(page, 'camera', {
		type: 'jarvis/test/camera_look',
		camera: 'Kitchen',
		after_ms: 2500
	});
	await expect(row).toHaveAttribute('data-state', 'live');
	await expect(page.getByTestId('caption')).toContainText('looking');
	await expect(page.getByTestId('caption')).toContainText('Kitchen');
	await expect(row).toHaveAttribute('data-state', 'done', { timeout: 8_000 });
	await expect(page.getByTestId('caption')).not.toContainText('looking');
});

test('a voice recognised, and a stranger refused, are rows too (M71)', async ({ page }) => {
	await gotoVoice(page);
	const heard = await untilRow(page, 'speaker', { type: 'jarvis/test/speaker_verdict', who: 'Ted' });
	await expect(heard).toContainText('Ted');
	await expect(heard).toContainText('2.31 / 8.83');
	await expect(heard).toHaveAttribute('data-state', 'done');
	// A stranger while enforcing: a failed row that says who they were nearest,
	// so a false reject of the owner can be read for what it was.
	await hook(page, { type: 'jarvis/test/speaker_verdict', deny: true });
	const refused = page.getByTestId('activity-row-speaker').filter({ hasText: 'not recognised' });
	await expect(refused).toHaveCount(1);
	await expect(refused).toContainText('refused · nearest owner');
	await expect(refused).toHaveAttribute('data-state', 'failed');
	// Too short to judge is not a stranger.
	await hook(page, { type: 'jarvis/test/speaker_verdict', unverifiable: true });
	const unverified = page.getByTestId('activity-row-speaker').filter({ hasText: 'unverified' });
	await expect(unverified).toHaveCount(1);
	await expect(unverified).toHaveAttribute('data-state', 'done');
});

test('a fact remembered and a moment landing are rows too', async ({ page }) => {
	await gotoVoice(page);
	const memory = await untilRow(page, 'memory', {
		type: 'jarvis/test/memory_change',
		action: 'remembered',
		text: 'They drink tea, not coffee.'
	});
	await expect(memory).toContainText('remembered');
	const moment = await untilRow(page, 'moment', {
		type: 'jarvis/test/moment',
		kind: 'reminder',
		title: 'Check the oven'
	});
	await expect(moment).toContainText('Check the oven');
	await expect(moment).toContainText('reminder');
});

test('a turn that read a remembered fact lights the graph on the voice tab', async ({ page }) => {
	await gotoVoice(page);
	const graph = page.getByTestId('voice-graph');
	await expect
		.poll(
			async () => {
				await hook(page, { type: 'jarvis/test/memory_used', entries: ['mem1'] });
				return graph.getAttribute('data-lit');
			},
			{ timeout: 20_000, intervals: [300, 700, 1500] }
		)
		.toBe('1');
	await expect(graph).toHaveAttribute('data-lit', '0', { timeout: 6_000 });
});

test('the strip keeps a dozen rows, newest first, and no more', async ({ page }) => {
	await gotoVoice(page);
	await untilRow(page, 'tool', {
		type: 'jarvis/test/tool_run',
		tools: Array.from({ length: 15 }, (_, i) => `tool_${i}`)
	});
	await expect
		.poll(async () => Number(await page.getByTestId('activity').getAttribute('data-count')), {
			timeout: 10_000
		})
		.toBe(12);
	const first = page.getByTestId('activity-row-tool').first();
	await expect(first).toContainText('tool_14');
});

test('under reduced motion nothing in the strip animates', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await gotoVoice(page);
	await untilRow(page, 'camera', { type: 'jarvis/test/camera_look', camera: 'Garden', after_ms: 4000 });
	const running = await page.getByTestId('activity').evaluate((el) => el.getAnimations({ subtree: true }).filter((a) => a.playState === 'running').length);
	expect(running).toBe(0);
});
