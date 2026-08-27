import { test, expect, type Page } from '@playwright/test';

/**
 * The approvals banner, when the held thing is a setting (M67).
 *
 * "How can I ask it to be able to edit settings with permission." The model
 * has `change_setting` now, Tier 3, and what the human sees on the card is
 * the decision: a card that read `key: llm.options.temperature · value: 0.2`
 * did not say what it was before, and "from what" is the whole question. So
 * jarvis-core composes one sentence from the PINNED arguments and carries it
 * as `summary`; the console shows that sentence as the headline, the tool's
 * name under it, and never the raw pairs. A request with no sentence — every
 * other tool — still draws name-and-arguments exactly as before, because the
 * phone and older cores send nothing else.
 */

const boot = async (page: Page) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
};

/**
 * Raise one the way the assistant would — jarvis-core fires
 * `jarvis_approval_required` when a tier-3 tool is held. The mock's hook takes
 * the same fields the real event carries, `summary` included.
 */
async function raise(page: Page, req: Record<string, unknown>): Promise<void> {
	await hook(page, { id: 99, type: 'test/raise_approval', ...req });
}

/** One frame to the mock's test hooks, from the page's own origin. */
async function hook(page: Page, frame: Record<string, unknown>): Promise<void> {
	await page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () => ws.send(JSON.stringify(payload));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		frame
	);
}

const SENTENCE = 'Change Wake word (voice.wake_word) from hey_jarvis to ok_nabu';

test('a held setting change reads as a sentence, and approving it changes the setting', async ({
	page
}) => {
	await boot(page);
	await page.goto('/settings/voice');
	const wake = page.getByTestId('plain-input-voice.wake_word');
	await expect(wake).toBeVisible({ timeout: 15_000 });
	await expect(wake).toHaveValue('hey_jarvis');

	// The event can fire before the layout's socket has subscribed, in which
	// case it reaches nobody; the request id is fixed, so re-raising is the
	// same request and not a second card.
	const held = {
		request_id: 'req-setting-1',
		tool: 'change_setting',
		description: 'Change one console setting. Needs the user\'s approval.',
		arguments: { key: 'voice.wake_word', value: 'ok_nabu', previous: 'hey_jarvis', label: 'Wake word' },
		summary: SENTENCE
	};
	await expect
		.poll(
			async () => {
				await raise(page, held);
				return page.getByTestId('approvals').isVisible();
			},
			{ timeout: 20_000, intervals: [300, 700, 1500, 2000] }
		)
		.toBe(true);

	// The sentence is the headline; the tool's name is still on the card; the
	// raw `key: value` line is not.
	const card = page.getByTestId('approval-change_setting');
	await expect(card.getByTestId('approval-summary-change_setting')).toHaveText(SENTENCE);
	await expect(card.getByTestId('approval-tool-change_setting')).toHaveText('change_setting');
	await expect(card.getByTestId('approval-args-change_setting')).toHaveCount(0);
	await expect(card).not.toContainText('key:');
	await expect(card).not.toContainText('"ok_nabu"');

	await card.getByTestId('approve-change_setting').click();
	await expect(page.getByTestId('approvals')).toHaveCount(0, { timeout: 10_000 });
	// The receipt says what was approved, not which function ran.
	await expect(page.getByTestId('toast').filter({ hasText: `Approved ${SENTENCE}` })).toBeVisible({
		timeout: 10_000
	});

	// Approving wrote through `config/settings/set`, so the settings page reads
	// the change: the row shows the new value from the overlay.
	await page.reload();
	await expect(page.getByTestId('plain-input-voice.wake_word')).toHaveValue('ok_nabu', {
		timeout: 15_000
	});
	await page.getByTestId('everything-summary').click();
	await expect(page.getByTestId('source-voice.wake_word')).toHaveText('overlay', { timeout: 10_000 });

	// Put the file's value back, so the next test starts where this one did.
	await page.getByTestId('plain-reset-voice.wake_word').click();
	await expect(page.getByTestId('source-voice.wake_word')).toHaveText('yaml', { timeout: 10_000 });
	await expect(page.getByTestId('error')).toHaveCount(0);
});

test('a request the server gave no sentence for still shows its name and its arguments', async ({
	page
}) => {
	await boot(page);
	await page.goto('/house/devices');
	await expect(page.getByTestId('entity-light.lab_lights')).toBeVisible({ timeout: 15_000 });

	await expect
		.poll(
			async () => {
				await raise(page, { request_id: 'req-plain-1', tool: 'lock_control' });
				return page.getByTestId('approvals').isVisible();
			},
			{ timeout: 20_000, intervals: [300, 700, 1500, 2000] }
		)
		.toBe(true);

	const card = page.getByTestId('approval-lock_control');
	await expect(card.getByTestId('approval-name-lock_control')).toHaveText('lock_control');
	await expect(card.getByTestId('approval-args-lock_control')).toContainText('lock.front_door');
	await expect(card.getByTestId('approval-summary-lock_control')).toHaveCount(0);

	await card.getByTestId('deny-lock_control').click();
	await expect(page.getByTestId('approvals')).toHaveCount(0, { timeout: 10_000 });
	await expect(page.getByTestId('error')).toHaveCount(0);
});

test('the Tools page test-run of change_setting is held with the same sentence, and an unknown key is refused with the nearest real ones', async ({
	page
}) => {
	await boot(page);
	await page.goto('/tools');
	// The native toolbox, whatever an earlier test left the mock saying.
	await hook(page, { id: 94, type: 'jarvis/test/tools_unsupported', unsupported: false });
	await page.reload();
	await expect(page.getByTestId('tool-select')).toBeVisible({ timeout: 15_000 });

	// Held, not run: the result says so and the card says what for.
	await page.getByTestId('tool-select').selectOption('change_setting');
	await page.getByTestId('tool-args').fill('{"key": "llm.options.temperature", "value": 0.2}');
	await page.getByTestId('tool-run').click();
	await expect(page.getByTestId('tool-result')).toContainText('approval_required', { timeout: 10_000 });
	await expect(page.getByTestId('approval-summary-change_setting')).toHaveText(
		'Change Temperature (llm.options.temperature) from 0.7 to 0.2',
		{ timeout: 10_000 }
	);
	await page.getByTestId('deny-change_setting').click();
	await expect(page.getByTestId('approvals')).toHaveCount(0, { timeout: 10_000 });

	// "Demo mode" is not a setting: the answer names what the settings are
	// called, and nothing is held for a human to deny.
	await page.getByTestId('tool-args').fill('{"key": "demo mode", "value": true}');
	await page.getByTestId('tool-run').click();
	await expect(page.getByTestId('tool-result')).toContainText("no setting called 'demo mode'", {
		timeout: 10_000
	});
	await expect(page.getByTestId('tool-result')).toContainText('the nearest are');
	await expect(page.getByTestId('approvals')).toHaveCount(0);
	await expect(page.getByTestId('error')).toHaveCount(0);
});
