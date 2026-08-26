import { test, expect, type Page } from '@playwright/test';

/**
 * The MODELS panel (M54): what the model servers actually serve.
 *
 * The settings page used to offer "Model" as a dropdown of the gateway's
 * aliases — `house`, `house-fast` — and called that the model. This panel
 * lists the served ids the mock's `jarvis/llm/models` answers with, the way
 * the real command answers on the deployed stack: a 27-B chat model behind
 * `house`, a 4-B fast one behind `house-fast`, a vision model on its own
 * server, the embedder and the reranker in theirs. What is asserted:
 *
 *   - the rows are the SERVED ids, and the alias is said beside the model it
 *     stands for, never listed as a model of its own;
 *   - the row says what the server said: family · size · quant in mono, the
 *     role as a tag, a lit dot only when the server says loaded, and the
 *     jobs it is used for in plain words — "not used by anything" included;
 *   - a size read off the id says "as named by the server";
 *   - choosing a model for a role writes the same setting the raw row would,
 *     and the next list reflects it (the conversation moves);
 *   - the panel has all four states: loading, empty, error, offline.
 */

const gotoAssistant = async (page: Page) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto('/settings/assistant');
	await expect(page.getByTestId('assistant-screen')).toBeVisible({ timeout: 15_000 });
};

/** Talk to the mock over its own socket, without the page's help. */
async function mockCommand(page: Page, payload: Record<string, unknown>): Promise<void> {
	await page.evaluate(async (frame) => {
		const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
		await new Promise((resolve) => socket.addEventListener('open', resolve));
		socket.send(JSON.stringify({ id: 1, ...frame }));
		await new Promise((resolve) => setTimeout(resolve, 200));
		socket.close();
	}, payload);
}

test.afterEach(async ({ page }) => {
	// Put the mock's model servers back for whichever test runs next.
	await mockCommand(page, { type: 'jarvis/test/models_mode', mode: 'ok' });
	await mockCommand(page, { type: 'config/settings/reset', key: 'llm.model' });
	await mockCommand(page, { type: 'config/settings/reset', key: 'llm.fast_model' });
});

test('the panel lists the served models, not the gateway aliases', async ({ page }) => {
	await gotoAssistant(page);
	const list = page.getByTestId('models-list');
	await expect(list).toBeVisible({ timeout: 15_000 });

	// The served ids, one row each — and no row for an alias.
	for (const id of ['qwen3.8-27b', 'qwen3-4b', 'qwen2.5vl:7b', 'BAAI/bge-small-en-v1.5', 'cross-encoder/ms-marco-MiniLM-L-6-v2']) {
		await expect(page.getByTestId(`model-${id}`), `${id} is not listed`).toBeVisible();
	}
	await expect(page.getByTestId('model-house')).toHaveCount(0);
	await expect(page.getByTestId('model-house-fast')).toHaveCount(0);

	// The chat model: name, family · size · quant, its alias, what it is used for.
	const chat = page.getByTestId('model-qwen3.8-27b');
	await expect(page.getByTestId('model-name-qwen3.8-27b')).toHaveText('Qwen 3.8 27B');
	await expect(page.getByTestId('model-meta-qwen3.8-27b')).toContainText('Qwen 3.8 · 27B · AWQ-INT4');
	await expect(page.getByTestId('model-meta-qwen3.8-27b')).toContainText('qwen3.8-27b');
	await expect(page.getByTestId('model-role-qwen3.8-27b')).toHaveText('chat');
	await expect(page.getByTestId('model-use-qwen3.8-27b')).toContainText('used for conversation, research and coding');
	await expect(page.getByTestId('model-use-qwen3.8-27b')).toContainText('as house at the gateway');
	// The size came off the id, and the row says so rather than presenting a guess as a fact.
	await expect(page.getByTestId('model-note-qwen3.8-27b')).toHaveText('as named by the server');
	await expect(chat).toHaveAttribute('data-loaded', 'loaded');

	// The fast model: configured, unloaded, used by nothing — all three said.
	const fast = page.getByTestId('model-qwen3-4b');
	await expect(page.getByTestId('model-role-qwen3-4b')).toHaveText('fast');
	await expect(fast).toHaveAttribute('data-loaded', 'not loaded');
	await expect(page.getByTestId('model-use-qwen3-4b')).toContainText('not used by anything');
	await expect(page.getByTestId('model-note-qwen3-4b')).toContainText('idle');

	// The other three roles, each with its tag and its job.
	await expect(page.getByTestId('model-role-qwen2.5vl:7b')).toHaveText('vision');
	await expect(page.getByTestId('model-use-qwen2.5vl:7b')).toContainText('used for vision');
	await expect(page.getByTestId('model-role-BAAI/bge-small-en-v1.5')).toHaveText('embeddings');
	await expect(page.getByTestId('model-role-cross-encoder/ms-marco-MiniLM-L-6-v2')).toHaveText('rerank');

	// The meta line is data, so it is set in mono; the name is not.
	const monoMeta = await page.getByTestId('model-meta-qwen3.8-27b').evaluate((el) => getComputedStyle(el).fontFamily.toLowerCase());
	expect(monoMeta).toContain('mono');
	const nameFace = await page.getByTestId('model-name-qwen3.8-27b').evaluate((el) => getComputedStyle(el).fontFamily.toLowerCase());
	expect(nameFace).not.toContain('mono');
});

test('the lit dot follows what the server says is loaded', async ({ page }) => {
	await gotoAssistant(page);
	await expect(page.getByTestId('models-list')).toBeVisible({ timeout: 15_000 });
	const dotOf = (id: string) =>
		page.getByTestId(`model-${id}`).locator('.dot').evaluate((el) => ({
			lit: el.classList.contains('lit'),
			background: getComputedStyle(el).backgroundColor
		}));
	const loaded = await dotOf('qwen3.8-27b');
	const idle = await dotOf('qwen3-4b');
	expect(loaded.lit).toBe(true);
	expect(idle.lit).toBe(false);
	expect(loaded.background).not.toBe(idle.background);
	// And a reader gets the word, not only the colour.
	await expect(page.getByTestId('model-loaded-qwen3.8-27b')).toHaveText('loaded');
	await expect(page.getByTestId('model-loaded-qwen3-4b')).toHaveText('not loaded');
});

test('a role choice writes the setting, and the next list moves the conversation', async ({ page }) => {
	// Watch the frames, so the claim is about the wire and not the toast.
	const sent: string[] = [];
	page.on('websocket', (ws) =>
		ws.on('framesent', (frame) => {
			if (typeof frame.payload === 'string') sent.push(frame.payload);
		})
	);
	await gotoAssistant(page);
	await expect(page.getByTestId('models-list')).toBeVisible({ timeout: 15_000 });

	// The chat choice offers the models by NAME and writes the value LLM_URL
	// knows them by: the alias, behind a gateway.
	const chat = page.getByTestId('role-chat');
	await expect(chat).toHaveValue('house');
	const options = await chat.locator('option').allTextContents();
	expect(options.some((o) => o.includes('Qwen 3 4B') && o.includes('house-fast'))).toBe(true);
	// An embedder is not something to run a conversation on.
	expect(options.some((o) => o.includes('bge'))).toBe(false);

	await chat.selectOption('house-fast');
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
		.toMatchObject({ key: 'llm.model', value: 'house-fast' });

	// The list was re-read, and the conversation now runs on the 4-B model.
	await expect(page.getByTestId('model-use-qwen3-4b')).toContainText('used for conversation', { timeout: 10_000 });
	await expect(page.getByTestId('model-role-qwen3-4b')).toHaveText('chat');
	await expect(page.getByTestId('model-use-qwen3.8-27b')).not.toContainText('conversation');

	// And the raw row behind EVERYTHING agrees — same setting, same API.
	await page.getByTestId('everything-summary').click();
	await expect(page.getByTestId('input-llm.model')).toHaveValue('house-fast');
	await expect(page.getByTestId('source-llm.model')).toHaveText('overlay');

	// The fast choice: "same as chat" is a real option, and choosing a model
	// writes llm.fast_model.
	const fast = page.getByTestId('role-fast');
	await expect(fast).toHaveValue('');
	await fast.selectOption('house');
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
				.filter((msg) => msg?.type === 'config/settings/set')
				.map((msg) => msg.key)
		)
		.toContain('llm.fast_model');
	await expect(page.getByTestId('input-llm.fast_model')).toHaveValue('house', { timeout: 10_000 });

	// Vision writes its own setting, from the vision models only.
	const vision = page.getByTestId('role-vision');
	await expect(vision).toHaveValue('qwen2.5vl:7b');
	const visionOptions = await vision.locator('option').allTextContents();
	expect(visionOptions.some((o) => o.includes('27B'))).toBe(false);

	await expect(page.getByTestId('error')).toHaveCount(0);
});

test('the panel shows it is loading rather than a blank', async ({ page }) => {
	// Everything answers except the models command: the section is ready, the
	// panel is still waiting — the window a skeleton exists for.
	await page.routeWebSocket(/\/ws$/, (ws) => {
		const server = ws.connectToServer();
		const held = new Set<number>();
		ws.onMessage((message) => {
			try {
				const frame = JSON.parse(String(message));
				if (frame.type === 'jarvis/llm/models') {
					held.add(frame.id);
					return;
				}
			} catch {
				/* binary or not ours */
			}
			server.send(message);
		});
		server.onMessage((message) => ws.send(message));
	});
	await gotoAssistant(page);
	await expect(page.getByTestId('group-assistant')).toBeVisible({ timeout: 15_000 });
	const panel = page.getByTestId('models');
	await expect(panel.locator('[data-screen-state="loading"]')).toBeVisible();
	await expect(panel.locator('[aria-busy="true"], [data-testid="skeleton"]').first()).toBeVisible();
	await expect(page.getByTestId('models-list')).toHaveCount(0);
});

test('a server that lists nothing is the empty state, in words', async ({ page }) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto('/');
	await mockCommand(page, { type: 'jarvis/test/models_mode', mode: 'empty' });
	await gotoAssistant(page);
	const empty = page.getByTestId('models-empty');
	await expect(empty).toBeVisible({ timeout: 15_000 });
	await expect(empty).toContainText('The model server lists nothing');
	await expect(page.getByTestId('models-list')).toHaveCount(0);
});

test('a failed read is the error state, with the reason and a retry that works', async ({ page }) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto('/');
	await mockCommand(page, { type: 'jarvis/test/models_mode', mode: 'error' });
	await gotoAssistant(page);
	const error = page.getByTestId('models-error');
	await expect(error).toBeVisible({ timeout: 15_000 });
	await expect(error).toContainText('Could not read the model servers');
	await expect(error).toContainText('502');
	// The rest of the section is untouched by the panel's failure.
	await expect(page.getByTestId('group-assistant')).toBeVisible();

	// Retry, once the servers are back.
	await mockCommand(page, { type: 'jarvis/test/models_mode', mode: 'ok' });
	await error.getByTestId('retry').click();
	await expect(page.getByTestId('models-list')).toBeVisible({ timeout: 10_000 });
	await expect(page.getByTestId('models-error')).toHaveCount(0);
});

test('when the link drops the panel says so and keeps the last list', async ({ page }) => {
	const sockets: { close: () => void }[] = [];
	await page.routeWebSocket(/\/ws$/, (ws) => {
		const server = ws.connectToServer();
		ws.onMessage((message) => server.send(message));
		server.onMessage((message) => ws.send(message));
		sockets.push(ws);
	});
	await gotoAssistant(page);
	await expect(page.getByTestId('models-list')).toBeVisible({ timeout: 15_000 });

	for (const socket of sockets.splice(0)) socket.close();

	await expect(page.getByTestId('models-offline')).toBeVisible({ timeout: 15_000 });
	// The rows are the last thing the server said, and they stay: hiding them
	// would make the offline copy a lie.
	await expect(page.getByTestId('model-qwen3.8-27b')).toBeVisible();
	// And the section's own banner is up too.
	await expect(page.getByTestId('link-dropped').first()).toBeVisible();
});
