import { test, expect, type Page } from '@playwright/test';

/**
 * Enrolment, complete (M71): the console's half.
 *
 * Whose voice Jarvis answers is a household now — one row per person, each
 * with a way to be forgotten; a sample is enrolled under a NAME; TEST MY VOICE
 * says who it heard; and the panel has its four states rather than vanishing
 * while it loads or when the read fails, which used to read as "this Jarvis
 * has no voice identity".
 *
 * Two kinds of assertion, deliberately kept apart. Requests that the console
 * relays under the console password — REMOVE, and the enrol and verify writes
 * — are asserted on their SHAPE (`page.route` answers them here) or on their
 * refusal, never on the unlocked happy path: the unlock limiter is
 * server-side and shared across the suite, and a test that depends on it
 * passes alone and fails in the run. The server's half of each write is
 * `jarvis-core/tests/test_speaker_gate.py`.
 */

const hook = (page: Page, payload: Record<string, unknown>) =>
	page.evaluate(
		(msg) =>
			new Promise((resolve) => {
				const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
				ws.onopen = () => ws.send(JSON.stringify({ id: 96, ...msg }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		payload
	);

const boot = async (page: Page) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
};

async function gotoVoice(page: Page) {
	await boot(page);
	await page.goto('/settings/voice');
	await expect(page.getByTestId('voice-identity')).toBeVisible({ timeout: 15_000 });
}

/** A status payload in the server's shape, for the states the mock cannot be put in. */
const STATUS = {
	supported: true,
	mode: 'observe',
	active: false,
	enrolled: false,
	person_enrolled: false,
	label: 'owner',
	people: [],
	min_samples: 3,
	measure_samples: 4,
	max_samples: 20,
	max_people: 8,
	max_label_chars: 40,
	default_label: 'owner',
	configured_threshold: null,
	samples: 0,
	prompts: ['Phrase one, as a question?', 'Phrase two, as an order.', 'Phrase three.']
};

test.afterEach(async ({ page }) => {
	// Back to one person, whatever a test did to the household.
	await hook(page, { type: 'jarvis/test/speaker_reset' }).catch(() => {});
});

test('the panel lists who is enrolled, one row each, and REMOVE forgets one person', async ({ page }) => {
	await gotoVoice(page);
	await hook(page, { type: 'jarvis/test/speaker_household' });
	await page.reload();
	await expect(page.getByTestId('person-owner')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('person-ted')).toBeVisible();
	await expect(page.getByTestId('person-samples-ted')).toContainText('4 of 20 samples');
	await expect(page.getByTestId('person-samples-ted')).toContainText('threshold 8.04');
	await expect(page.getByTestId('speaker-samples')).toContainText('2 people · 9 samples');
	await expect(page.getByTestId('settings-voice-lede')).toContainText('2 voices enrolled');

	// REMOVE is one person, by name, on the wire — and it is behind the console
	// password like FORGET, so without unlocking it is refused and both rows
	// stay. The refusal IS the feature: this relay attaches the admin token.
	const request = page.waitForRequest((r) => r.method() === 'DELETE' && r.url().includes('/api/voice/speaker'));
	await page.getByTestId('person-remove-ted').click();
	expect(new URL((await request).url()).searchParams.get('label')).toBe('Ted');
	await expect(page.getByTestId('speaker-error')).toContainText(/unlock the console/i);
	await expect(page.getByTestId('person-ted')).toBeVisible();
	await expect(page.getByTestId('person-owner')).toBeVisible();
	// And FORGET now says what it does to a household.
	await expect(page.getByTestId('voice-identity')).toContainText('Forget everyone');
});

test('enrolling under a name sends the name with every sample, and a new name is a new person', async ({
	page,
	context
}) => {
	await context.grantPermissions(['microphone']);
	await gotoVoice(page);
	// The write is answered here, in the server's shape, so what is asserted
	// is what the component SENDS rather than whether the console is unlocked.
	const sent: string[] = [];
	await page.route('**/api/voice/speaker/enrol**', async (route) => {
		sent.push(route.request().url());
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ ...STATUS, label: 'Ted', samples: 1, accepted: true, sample: { speech_ms: 1400, has_pitch: true, vector: null } })
		});
	});
	await expect(page.getByTestId('enrol-voice')).toContainText('Enrol owner again');
	await page.getByTestId('enrol-name').fill('Ted');
	await expect(page.getByTestId('enrol-voice')).toContainText('Enrol Ted');
	await expect(page.getByTestId('enrol-voice')).toContainText('a new name is a new person');
	await page.getByTestId('enrol-start').click();
	await expect(page.getByTestId('enrol-voice')).toContainText('Enrolling Ted');
	await page.getByTestId('enrol-record-0').click();
	await expect(page.getByTestId('enrol-stop-0')).toBeVisible();
	await page.waitForTimeout(1200);
	await page.getByTestId('enrol-stop-0').click();
	await expect(page.getByTestId('enrol-record-0')).toHaveText('AGAIN');
	// Two writes per phrase since M79: "recording now" before the microphone
	// opens, then the sample — the heartbeat carries no name, the sample does.
	expect(sent).toHaveLength(2);
	expect(sent[0]).toContain('/api/voice/speaker/enrolling');
	const query = new URL(sent[1]).searchParams;
	expect(query.get('label')).toBe('Ted');
	expect(query.get('rate')).toBe('16000');
	expect(query.get('width')).toBe('2');
});

test('a name that cannot be one is refused before anything is recorded, in the server\'s words', async ({ page }) => {
	await gotoVoice(page);
	await page.getByTestId('enrol-name').fill('x'.repeat(41));
	await expect(page.getByTestId('enrol-name-problem')).toHaveText('a name is at most 40 characters');
	await expect(page.getByTestId('enrol-start')).toBeDisabled();
	await page.getByTestId('enrol-name').fill('  Ted  ');
	await expect(page.getByTestId('enrol-name-problem')).toHaveCount(0);
	await expect(page.getByTestId('enrol-start')).toBeEnabled();
});

test('TEST says who it heard and what enforcement would have done', async ({ page, context }) => {
	await context.grantPermissions(['microphone']);
	await gotoVoice(page);
	let verifyUrl = '';
	await page.route('**/api/voice/speaker/verify**', async (route) => {
		verifyUrl = route.request().url();
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				verdict: { accepted: true, label: 'Ted', nearest: 'Ted', score: 2.314, threshold: 8.831, confidence: 0.9, reason: 'match', blocks: { timbre: 2.1, variability: 1.6, pitch: 3.2 } },
				would_block: false,
				mode: 'observe'
			})
		});
	});
	await page.getByTestId('enrol-test').click();
	await expect(page.getByTestId('enrol-test-listening')).toBeVisible();
	await page.waitForTimeout(1200);
	await page.getByTestId('enrol-test-stop').click();
	await expect(page.getByTestId('enrol-test-result')).toHaveText(
		'Recognised as Ted · 2.31 against 8.83 · this turn would be allowed'
	);
	// The three blocks behind it, so a person can see which part of their voice the gate weighed (M105).
	await expect(page.getByTestId('enrol-test-blocks')).toHaveText('timbre 2.10 · variability 1.60 · pitch 3.20');
	// Compared with EVERYONE: no `label` on a test, or it could not say who.
	expect(new URL(verifyUrl).searchParams.get('label')).toBeNull();
});

test('TEST names the block that refused on its own, and shows the three (M105)', async ({ page, context }) => {
	await context.grantPermissions(['microphone']);
	await gotoVoice(page);
	await page.route('**/api/voice/speaker/verify**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				verdict: { accepted: false, label: null, nearest: 'Ted', score: 5.16, threshold: 4.93, confidence: 0.3, reason: 'pitch-mismatch', blocks: { timbre: 3.4638, variability: 2.6567, pitch: 9.3512 } },
				would_block: false,
				mode: 'observe'
			})
		});
	});
	await page.getByTestId('enrol-test').click();
	await page.waitForTimeout(1200);
	await page.getByTestId('enrol-test-stop').click();
	await expect(page.getByTestId('enrol-test-result')).toHaveText(
		'Not recognised (nearest: Ted; pitch far out) · 5.16 against 4.93 · the gate is not enforcing, so nothing would be blocked'
	);
	await expect(page.getByTestId('enrol-test-blocks')).toHaveText('timbre 3.46 · variability 2.66 · pitch 9.35');
});

test('TEST from a locked console is refused, and says how to unlock it', async ({ page, context }) => {
	await context.grantPermissions(['microphone']);
	await gotoVoice(page);
	await page.getByTestId('enrol-test').click();
	await page.waitForTimeout(1200);
	await page.getByTestId('enrol-test-stop').click();
	await expect(page.getByTestId('enrol-error')).toContainText(/console password/i);
});

test('the panel has its four states: loading, error, offline, and nobody enrolled', async ({ page }) => {
	await boot(page);
	// Loading: the read is held open, and the panel says so rather than vanishing.
	let release: (() => void) | null = null;
	await page.route('**/api/voice/speaker', async (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		await new Promise<void>((resolve) => (release = resolve));
		await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ message: 'the Jarvis server answered 500' }) });
	});
	await page.goto('/settings/voice');
	await expect(page.getByTestId('speaker-loading')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('settings-voice-lede')).toContainText('reading whose voice');
	// The held read has to have ARRIVED before it can be released: on CI the
	// loading state showed before the request reached the route twice, and
	// `release` was still null.
	await expect.poll(() => typeof release === 'function', { timeout: 15_000 }).toBe(true);
	// Error: the server's own words, and a Retry.
	release!();
	await expect(page.getByTestId('speaker-error-state')).toBeVisible();
	await expect(page.getByTestId('speaker-error-state')).toContainText('the Jarvis server answered 500');
	// Offline: no answer at all.
	await page.unroute('**/api/voice/speaker');
	await page.route('**/api/voice/speaker', (route) =>
		route.request().method() === 'GET' ? route.abort('failed') : route.continue()
	);
	await page.getByTestId('speaker-error-state').getByRole('button').click();
	await expect(page.getByTestId('speaker-offline')).toBeVisible();
	// Nobody enrolled: the gate's own empty state, with the way in still offered.
	await page.unroute('**/api/voice/speaker');
	await page.route('**/api/voice/speaker', (route) =>
		route.request().method() === 'GET'
			? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(STATUS) })
			: route.continue()
	);
	await page.getByTestId('speaker-offline').getByRole('button').click();
	await expect(page.getByTestId('speaker-samples')).toContainText('nobody — the gate is inert');
	await expect(page.getByTestId('enrol-start')).toBeEnabled();
	await expect(page.getByTestId('enrol-test')).toBeDisabled();
	await expect(page.getByTestId('speaker-forget')).toBeDisabled();
	// And a server with no voice identity at all is its own sentence, not "nobody".
	await page.unroute('**/api/voice/speaker');
	await page.route('**/api/voice/speaker', (route) =>
		route.request().method() === 'GET'
			? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ supported: false, enrolled: false, mode: 'off', active: false }) })
			: route.continue()
	);
	await page.reload();
	await expect(page.getByTestId('speaker-unsupported')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('speaker-unsupported')).toContainText('Update jarvis-core');
});

test('a configured threshold is shown as the one in force, beside what enrolment suggests', async ({ page }) => {
	await boot(page);
	await page.route('**/api/voice/speaker', (route) =>
		route.request().method() === 'GET'
			? route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({
						...STATUS,
						enrolled: true,
						person_enrolled: true,
						active: true,
						samples: 5,
						threshold: 8.8,
						configured_threshold: 8.8,
						suggested_threshold: 9.005,
						worst_self_score: 7.2,
						threshold_measured: true,
						people: [{ label: 'owner', enrolled: true, samples: 5, max_samples: 20, threshold: 8.8, worst_self_score: 7.2, suggested_threshold: 9.005, threshold_measured: true }]
					})
				})
			: route.continue()
	);
	await page.goto('/settings/voice');
	await expect(page.getByTestId('speaker-threshold')).toContainText('8.8', { timeout: 15_000 });
	await expect(page.getByTestId('speaker-threshold-configured')).toContainText('set in configuration.yaml; enrolment suggests 9.005');
});
