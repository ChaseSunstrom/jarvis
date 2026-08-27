import { test, expect, type Page } from '@playwright/test';
import { SCREENS, sectionsOf } from '../src/lib/screens';
import { FEATURED, FEATURED_KEYS, sectionOfGroup } from '../src/lib/sections/settingsPlan';

/**
 * SETTINGS, cut to what a person changes (M54).
 *
 * Five sections a person can name — Assistant · Voice · House · Console ·
 * Tools — each opening on a few rows in plain words with one line saying why,
 * and the rest of what the server sends behind EVERYTHING, exactly as the
 * server describes it. Two claims, and the second is the one that matters:
 *
 *   1. the plain rows are there, in words, with a why, and they save;
 *   2. **nothing was lost** — every setting the mock backend sends is on one
 *      of the five sections, as a plain row or behind EVERYTHING, and every
 *      panel the old single page carried (pairing, voice identity, this
 *      console, text size, the event stream, the desktop) is reachable.
 *
 * The list of what the server sends is read from the mock at run time, not
 * typed here, so a setting added to jarvis-core and to the mock is checked
 * without anybody remembering this file.
 */

const boot = async (page: Page) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
};

const SETTINGS_SECTIONS = sectionsOf('/settings');

test('SETTINGS has six sections, in order, each a real page', async ({ page }) => {
	await boot(page);
	await page.goto('/settings');
	await expect(page).toHaveURL(/\/settings\/assistant$/);

	const strip = page.locator('nav[aria-label="Sections"] a');
	await expect(strip).toHaveText(['ASSISTANT', 'VOICE', 'HOUSE', 'CONSOLE', 'SYSTEM', 'TOOLS'], { ignoreCase: true });
	expect(SETTINGS_SECTIONS.map((s) => s.path)).toEqual([
		'/settings/assistant',
		'/settings/voice',
		'/settings/house',
		'/settings/console',
		'/settings/system',
		'/settings/tools'
	]);

	for (const section of SETTINGS_SECTIONS) {
		await page.goto(section.path);
		await expect(page.getByTestId(section.probe), `${section.name} never became ready`).toBeVisible({ timeout: 15_000 });
		await expect(page.locator('nav[aria-label="Sections"] a[aria-current="page"]')).toHaveAttribute('href', section.path);
	}
});

test('the plain rows are words with a why, and they save', async ({ page }) => {
	await boot(page);
	for (const [section, rows] of Object.entries(FEATURED)) {
		if (!rows.length) continue;
		const screen = SCREENS.find((s) => s.path === `/settings/${section}`)!;
		await page.goto(screen.path);
		await expect(page.getByTestId(screen.probe)).toBeVisible({ timeout: 15_000 });
		for (const row of rows) {
			const plain = page.getByTestId(`plain-${row.key}`);
			await expect(plain, `${row.key} is not a plain row on ${section}`).toBeVisible({ timeout: 15_000 });
			await expect(plain.locator('b')).toHaveText(row.label);
			await expect(plain.locator('.why')).toHaveText(row.why);
			// The key is for the raw row; a plain row does not show it.
			await expect(plain).not.toContainText(row.key);
			// And nothing to press until something changes.
			await expect(page.getByTestId(`plain-save-${row.key}`)).toBeDisabled();
		}
	}

	// One save, through a plain row, lands on the wire as the setting it is.
	const sent: string[] = [];
	page.on('websocket', (ws) =>
		ws.on('framesent', (frame) => {
			if (typeof frame.payload === 'string') sent.push(frame.payload);
		})
	);
	await page.goto('/settings/voice');
	const wake = page.getByTestId('plain-input-voice.wake_word');
	await expect(wake).toBeVisible({ timeout: 15_000 });
	await wake.selectOption('ok_nabu');
	await page.getByTestId('plain-save-voice.wake_word').click();
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
		.toMatchObject({ key: 'voice.wake_word', value: 'ok_nabu' });
	// Saved: the raw row agrees and offers a RESET, which puts the file's value back.
	await page.getByTestId('everything-summary').click();
	await expect(page.getByTestId('source-voice.wake_word')).toHaveText('overlay', { timeout: 10_000 });
	await page.getByTestId('plain-reset-voice.wake_word').click();
	await expect(page.getByTestId('source-voice.wake_word')).toHaveText('yaml', { timeout: 10_000 });
	await expect(wake).toHaveValue('hey_jarvis');
});

test('every setting the server sends is reachable: plain, or behind EVERYTHING', async ({ page }) => {
	await boot(page);
	await page.goto('/settings/assistant');
	await expect(page.getByTestId('assistant-screen')).toBeVisible({ timeout: 15_000 });

	// What the server sends, read from the mock over its own socket.
	const rows: { key: string; group: string }[] = await page.evaluate(async () => {
		const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
		await new Promise((resolve) => socket.addEventListener('open', resolve));
		const answer = new Promise<any>((resolve) =>
			socket.addEventListener('message', (m) => {
				const frame = JSON.parse(String(m.data));
				if (frame.id === 7) resolve(frame.result);
			})
		);
		socket.send(JSON.stringify({ id: 7, type: 'config/settings/list' }));
		const result = await answer;
		socket.close();
		return result.settings.map((r: any) => ({ key: r.key, group: r.group }));
	});
	expect(rows.length).toBeGreaterThan(8);

	const bySection = new Map<string, string[]>();
	for (const row of rows) {
		const section = sectionOfGroup(row.group);
		bySection.set(section, [...(bySection.get(section) ?? []), row.key]);
	}

	for (const [section, keys] of bySection) {
		await page.goto(`/settings/${section}`);
		const fold = page.getByTestId('everything');
		await expect(fold).toBeVisible({ timeout: 15_000 });
		// Closed by default: the page opens on the few rows a person came for.
		await expect(fold).not.toHaveAttribute('open', /.*/);
		await page.getByTestId('everything-summary').click();
		await expect(fold).toHaveAttribute('open', /.*/);
		for (const key of keys) {
			const raw = page.getByTestId(`setting-${key}`);
			await expect(raw, `${key} (group → ${section}) is not behind EVERYTHING`).toBeVisible();
			// The raw row is the server's row: the key, the source, SAVE.
			await expect(raw).toContainText(key);
			await expect(page.getByTestId(`source-${key}`)).toBeVisible();
			await expect(page.getByTestId(`save-${key}`)).toBeVisible();
		}
		// And every row this section FEATURES is a plain row above the fold —
		// whichever group the server filed it under (`jarvis.name` is House to
		// the server and Assistant to a person).
		for (const row of FEATURED[section as keyof typeof FEATURED]) {
			await expect(page.getByTestId(`plain-${row.key}`)).toBeVisible();
			expect(FEATURED_KEYS.has(row.key)).toBe(true);
		}
	}
});

test('every panel the one long page carried is still somewhere', async ({ page }) => {
	await boot(page);

	// Voice: whose voice, with enrolment.
	await page.goto('/settings/voice');
	await expect(page.getByTestId('voice-identity')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('speaker-forget')).toBeVisible();
	await expect(page.getByTestId('enrol-start')).toBeVisible();

	// Console: text size, this console, pairing, the desktop, the event stream.
	await page.goto('/settings/console');
	await expect(page.getByTestId('settings-console-lede')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('text-size')).toBeVisible();
	await expect(page.getByTestId('console-env')).toBeVisible();
	await expect(page.getByTestId('backend-kind')).toHaveText('core', { timeout: 15_000 });
	await expect(page.getByTestId('pairing')).toBeVisible();
	await expect(page.getByTestId('tokens')).toBeVisible();
	await expect(page.getByTestId('this-window')).toBeVisible();
	await expect(page.getByTestId('shell-absent')).toBeVisible();
	await expect(page.getByTestId('paired-computers')).toBeVisible();
	await expect(page.getByTestId('desktop-devices')).toContainText('Workshop Desktop');
	const stream = page.getByTestId('event-stream');
	await expect(stream).toBeVisible();
	await expect(stream).not.toHaveAttribute('open', /.*/);

	// House: the rooms are a link to where they are managed, not a second editor.
	await page.goto('/settings/house');
	await expect(page.getByTestId('areas-link')).toHaveAttribute('href', '/house/areas');

	// The old desktop page, and the older /desktop, both land on Console.
	await page.goto('/settings/desktop');
	await expect(page).toHaveURL(/\/settings\/console$/);
	await page.goto('/desktop');
	await expect(page).toHaveURL(/\/settings\/console$/);
});

test('the fold and the plain rows are one truth: a raw save shows on the plain row', async ({ page }) => {
	await boot(page);
	await page.goto('/settings/house');
	await expect(page.getByTestId('plain-jarvis.unit_system')).toBeVisible({ timeout: 15_000 });
	await page.getByTestId('everything-summary').click();
	await page.getByTestId('input-jarvis.unit_system').selectOption('imperial');
	await page.getByTestId('save-jarvis.unit_system').click();
	await expect(page.getByTestId('source-jarvis.unit_system')).toHaveText('overlay', { timeout: 10_000 });
	await expect(page.getByTestId('plain-input-jarvis.unit_system')).toHaveValue('imperial');
	await page.getByTestId('reset-jarvis.unit_system').click();
	await expect(page.getByTestId('plain-input-jarvis.unit_system')).toHaveValue('metric', { timeout: 10_000 });

	// A package-owned row is locked on both, and says which file to edit.
	await expect(page.getByTestId('plain-input-jarvis.time_zone')).toBeDisabled();
	await expect(page.getByTestId('plain-package-jarvis.time_zone')).toContainText('packages/house.yaml');
	await expect(page.getByTestId('input-jarvis.time_zone')).toBeDisabled();
	await expect(page.getByTestId('error')).toHaveCount(0);
});

// M70: the pace is on Settings › Voice, as a number with the honest sentence —
// Piper takes it at start, so the row names the real knob (PIPER_LENGTH_SCALE)
// instead of promising a live change. The operator judges capability from
// this screen; a pace the house speaks at but the screen cannot name is, to
// them, a pace that cannot be set.
test('the voice pace is on Settings › Voice, as a number, saying where the knob is', async ({ page }) => {
	await page.goto('/settings/voice');
	const pace = page.getByTestId('plain-input-voice.tts.length_scale');
	await expect(pace).toBeVisible({ timeout: 10_000 });
	await expect(pace).toHaveValue('0.9');
	const row = page.getByTestId('plain-voice.tts.length_scale');
	await expect(row).toContainText(/pace/i);
	await expect(row).toContainText(/PIPER_LENGTH_SCALE/);
});

// Who may speak (M71's follow-up): the gate's mode is a setting on Settings ›
// Voice, choosable before anyone is enrolled — the operator "couldn't set the
// enrol mode when enrolling" because the screen only showed it.
test('the voice gate\'s mode is a choice on Settings › Voice, with the three modes', async ({ page }) => {
	await page.goto('/settings/voice');
	const mode = page.getByTestId('plain-input-voice.speaker.mode');
	await expect(mode).toBeVisible({ timeout: 10_000 });
	await expect(mode.locator('option')).toHaveCount(3);
	await expect(page.getByTestId('plain-voice.speaker.mode')).toContainText(/once a voice is enrolled/i);
});
