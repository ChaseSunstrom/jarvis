import { test, expect, type Page } from '@playwright/test';

/**
 * The voice HUD, as a surface somebody actually has to use.
 *
 * Everything here is about `/` specifically. The console got the attention —
 * approvals, a palette, a keyboard map — and the page that is on screen when
 * Jarvis is being spoken to was left as a picture of an orb: an approval raised
 * by the turn you had just spoken was invisible until you navigated away, the
 * palette shortcut was swallowed and did nothing, a long answer had no scroll
 * path to the mute button under it, and a denied microphone ended the
 * conversation with no other way to say anything.
 */

/** Raise a tier-3 approval the way jarvis-core does, from inside the page. */
async function raiseApproval(page: Page, tool: string, requestId: string): Promise<void> {
	await page.evaluate(
		([t, rid]) =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () =>
					ws.send(JSON.stringify({ id: 99, type: 'test/raise_approval', tool: t, request_id: rid }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		[tool, requestId]
	);
}

test('a held action is answerable on the HUD, without it becoming the console', async ({
	page
}) => {
	// The banner's own docstring says a request can arrive "mid-sentence on the
	// HUD". It could not: the layout rendered it in the console branch only, and
	// stopped the socket it rides on the moment you were on `/`. A tier-3 gate
	// raised by a voice turn — which is the ONLY way most of them are raised —
	// waited unanswered in front of somebody standing at the orb.
	await page.goto('/');
	await expect(page.getByTestId('mic')).toBeVisible({ timeout: 10_000 });

	// Raised until it lands. The HUD shows no link indicator — that is the point
	// of it being bare — so there is nothing to wait for that says the layout's
	// socket has finished subscribing, and an event fired before that reaches
	// nobody. The request id is fixed, so re-raising is the same request.
	await expect
		.poll(
			async () => {
				await raiseApproval(page, 'lock_control', 'req-hud-1');
				return page.getByTestId('approvals').isVisible();
			},
			{ timeout: 20_000, intervals: [300, 700, 1500, 2000] }
		)
		.toBe(true);

	await expect(page.getByTestId('approval-args-lock_control')).toContainText('lock.front_door');

	// Still the HUD. No nav, no console frame — the orb is still the page.
	await expect(page.getByTestId('nav-devices')).toHaveCount(0);
	await expect(page.getByTestId('orb')).toBeVisible();

	// The buttons are dressed, not browser defaults. `.btn` is declared under
	// `.console` in chrome.css, and the dock is not inside a `.console`; if that
	// rule stops reaching here, APPROVE and DENY become grey system buttons on a
	// black screen at the one moment somebody has to tell them apart.
	const approve = page.getByTestId('approve-lock_control');
	const font = await approve.evaluate((el) => getComputedStyle(el).fontFamily.toLowerCase());
	expect(font, 'the approvals dock has lost the console furniture').toContain('mono');

	await approve.click();
	await expect(page.getByTestId('approvals')).toHaveCount(0, { timeout: 10_000 });
});

test('what a turn is doing is visible on the HUD, which is where the turn was spoken', async ({
	page
}) => {
	// Same reasoning as the approvals dock, and the same fix: the strip that
	// names each tool as it runs was rendered in the console branch only, so the
	// nine seconds a turn spends touching the house looked, from the surface you
	// spoke to, like nothing happening at all.
	await page.goto('/');
	await expect(page.getByTestId('mic')).toBeVisible({ timeout: 10_000 });

	const runTools = () =>
		page.evaluate(
			(names) =>
				new Promise((resolve) => {
					const ws = new WebSocket(
						`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
					);
					ws.onopen = () =>
						ws.send(JSON.stringify({ id: 98, type: 'jarvis/test/tool_run', tools: names }));
					ws.onmessage = () => {
						ws.close();
						resolve(null);
					};
				}),
			['get_state', 'lock_control']
		);

	// Repeated for the same reason as the approval above: nothing on this page
	// reports that the layout's socket has finished subscribing, and a round
	// broadcast before it did reaches nobody.
	await expect
		.poll(
			async () => {
				await runTools();
				return page.getByTestId('tool-activity').isVisible();
			},
			{ timeout: 20_000, intervals: [300, 700, 1500, 2000] }
		)
		.toBe(true);

	await expect(page.getByTestId('tool-row-lock_control')).toBeVisible();
	await expect(page.getByTestId('nav-devices')).toHaveCount(0);
});

test('the palette shortcut is left to the browser on the HUD, and taken in the console', async ({
	page
}) => {
	// Ctrl/Cmd-K was preventDefault()ed on every route, and the palette it
	// toggled only exists in the console branch — so on `/` the key blocked
	// whatever the browser does with it and gave nothing back.
	const watch = () =>
		page.evaluate(() => {
			(window as any).__prevented = null;
			window.addEventListener('keydown', (e) => {
				if (e.key.toLowerCase() === 'k' && (e.ctrlKey || e.metaKey)) {
					// Registered after the app's own handler, so this sees what the
					// app decided to do with the event.
					(window as any).__prevented = e.defaultPrevented;
				}
			});
		});

	await page.goto('/');
	await expect(page.getByTestId('mic')).toBeVisible({ timeout: 10_000 });
	await watch();
	await page.keyboard.press('Control+k');
	expect(await page.evaluate(() => (window as any).__prevented)).toBe(false);
	await expect(page.getByTestId('palette')).toHaveCount(0);

	await page.goto('/devices');
	await expect(page.getByTestId('palette-open')).toBeVisible();
	await watch();
	await page.keyboard.press('Control+k');
	expect(await page.evaluate(() => (window as any).__prevented)).toBe(true);
	await expect(page.getByTestId('palette')).toBeVisible();
});

test('the HUD scrolls to its controls on a short screen instead of clipping them', async ({
	page
}) => {
	// `height: 100dvh; overflow: hidden` on a four-row grid. A laptop in
	// landscape, or a long answer, pushed the readout and the mute button past
	// the bottom edge with no way to reach either.
	await page.setViewportSize({ width: 900, height: 360 });
	await page.goto('/');
	const mic = page.getByTestId('mic');
	await expect(mic).toBeVisible({ timeout: 10_000 });

	const overflow = await page.evaluate(
		() => getComputedStyle(document.querySelector('main')!).overflowY
	);
	expect(overflow, 'the HUD is clipping its own content again').not.toBe('hidden');

	const scrollable = await page.evaluate(
		() => document.documentElement.scrollHeight > window.innerHeight
	);
	expect(scrollable, 'nothing overflowed, so this viewport proves nothing').toBe(true);

	await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
	const reached = await mic.evaluate((el) => {
		const box = el.getBoundingClientRect();
		return box.top >= 0 && box.bottom <= window.innerHeight + 1;
	});
	expect(reached, 'the mute button cannot be scrolled to').toBe(true);
});

test('a refused microphone leaves a way to speak, and says so out loud', async ({ page }) => {
	// Two defects in one screen. The button's `aria-label` said "Mute the
	// microphone" over visible text reading MIC BLOCKED — ALLOW IT IN THE
	// BROWSER, so the sighted user and the screen-reader user were told
	// different things; and there was no `<input>` on this page at all, so the
	// answer to a denied prompt was that Jarvis could not be addressed at all.
	await page.addInitScript(() => {
		Object.defineProperty(navigator, 'mediaDevices', {
			configurable: true,
			value: {
				getUserMedia: () =>
					Promise.reject(Object.assign(new Error('denied'), { name: 'NotAllowedError' }))
			}
		});
	});

	const sent: string[] = [];
	page.on('websocket', (ws) =>
		ws.on('framesent', (frame) => {
			if (typeof frame.payload === 'string') sent.push(frame.payload);
		})
	);

	await page.goto('/');
	const mic = page.getByTestId('mic');
	await expect(mic).toContainText('MIC BLOCKED', { timeout: 10_000 });
	// The accessible name IS the visible text now, rather than an override that
	// hid it.
	await expect(mic).toHaveAccessibleName(/MIC BLOCKED/);
	await expect(mic).toHaveAttribute('data-mic', 'closed');

	await page.getByTestId('text-input').fill('turn on the lab lights');
	await page.getByTestId('text-send').click();

	// What was typed is what the readout says was said.
	await expect(page.getByTestId('transcript')).toContainText('turn on the lab lights');
	await expect(page.getByTestId('text-input')).toHaveValue('');

	// ...and it went out as a real pipeline run, entered at the intent stage
	// because there is no audio to transcribe.
	await expect
		.poll(
			() =>
				sent
					.map((frame) => {
						try {
							return JSON.parse(frame);
						} catch {
							return null;
						}
					})
					.find((msg) => msg?.type === 'assist_pipeline/run' && msg?.start_stage === 'intent'),
			{ message: 'no intent-stage run was sent for the typed turn' }
		)
		.toMatchObject({ end_stage: 'tts', input: { text: 'turn on the lab lights' } });
});

test('the orb stops moving when the reader has asked for reduced motion', async ({ page }) => {
	// The CSS kill switch in base.css cannot reach a requestAnimationFrame loop,
	// so the largest and brightest moving object in the app — three drifting
	// blobs, two counter-rotating rings, a radar sweep and a breathing core —
	// was the one thing that ignored the setting outright.
	//
	// `emulateMedia` rather than the `reducedMotion` fixture: the fixture did not
	// reach the page in this configuration (matchMedia still reported false
	// inside it), and a test that silently emulates nothing is a test that
	// passes whatever the orb does.
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await page.goto('/');
	const orb = page.getByTestId('orb');
	await expect(orb).toBeVisible({ timeout: 10_000 });
	expect(
		await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches),
		'the preference never reached the page'
	).toBe(true);
	expect(
		await orb.evaluate((el) => el.tagName),
		'no WebGL here, so this proves nothing about the shader loop'
	).toBe('CANVAS');

	// Wait for the warm-up to finish, then measure FRAMES.
	//
	// This used to be a fixed 1.2 s settle followed by comparing two PNGs 700 ms
	// apart, and it failed on CI while passing everywhere else. The settle was
	// the problem: it is a magic number tuned on a machine with a GPU, and a
	// cold runner compiling this shader on SwiftShader can still be drawing its
	// FIRST frame when the stopwatch starts. Every draw advances `uTime` and the
	// blob phases, so one late warm-up frame is byte-for-byte indistinguishable
	// from an animation — the test could not tell "slow to start" from "never
	// stopped".
	//
	// `data-frames` answers the actual question. A paused orb's count is
	// constant however long the first frame took; an animating one climbs at
	// display rate. Nothing here is weaker than before: a genuinely animating
	// orb never settles, so `settle` exhausts its deadline and the strict
	// comparisons below still run and still fail.
	const frames = async () => Number(await orb.getAttribute('data-frames'));
	const settle = async (deadlineMs = 15_000) => {
		const until = Date.now() + deadlineMs;
		let seen = await frames();
		while (Date.now() < until) {
			await page.waitForTimeout(250);
			const now = await frames();
			if (now === seen) return true;
			seen = now;
		}
		return false;
	};
	expect(await settle(), 'the orb never stopped drawing').toBe(true);

	const before = await frames();
	expect(before, 'the orb never drew at all, so this proves nothing').toBeGreaterThan(0);
	const boxBefore = await orb.boundingBox();
	const first = await orb.screenshot();
	await page.waitForTimeout(700);
	const second = await orb.screenshot();

	// The direct measurement: no frames at all in the window.
	expect(await frames(), 'the orb is still drawing frames').toBe(before);
	// And the pixels agree, which also covers anything drawn outside the loop.
	expect(await orb.boundingBox(), 'the page moved under the orb').toEqual(boxBefore);
	expect(Buffer.compare(first, second), 'the orb is still animating').toBe(0);
});

test('the orb does move when nobody has asked it not to', async ({ page }) => {
	// The other half of the pair: reduced motion must be the reason it stopped,
	// not a shader that quietly stopped drawing.
	await page.goto('/');
	const orb = page.getByTestId('orb');
	await expect(orb).toBeVisible({ timeout: 10_000 });
	expect(await orb.evaluate((el) => el.tagName)).toBe('CANVAS');

	// The counter-check, measured the same way, so the pair cannot both pass by
	// the orb simply never drawing.
	const frames = async () => Number(await orb.getAttribute('data-frames'));
	await expect
		.poll(frames, { timeout: 10_000, message: 'the orb never drew a frame' })
		.toBeGreaterThan(0);

	const before = await frames();
	const first = await orb.screenshot();
	await page.waitForTimeout(700);
	const second = await orb.screenshot();
	expect(await frames(), 'the orb stopped drawing on its own').toBeGreaterThan(before);
	expect(Buffer.compare(first, second), 'the orb is not animating at all').not.toBe(0);
});
