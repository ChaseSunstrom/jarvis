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
	await expect(page.getByTestId('nav-house')).toHaveCount(0);
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
	await expect(page.getByTestId('nav-house')).toHaveCount(0);
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
	expect(await orb.boundingBox(), 'the page moved under the orb').toEqual(boxBefore);

	// And the pixels agree, which also covers anything drawn outside the loop.
	//
	// MEASURED, not byte-compared. This was `Buffer.compare(first, second) === 0`
	// and it went red intermittently — deep in a full run, never on its own —
	// with the frame counter above passing in the same breath. The orb had drawn
	// exactly one frame and drawn nothing since; the DOM over that spot was
	// static; the box had not moved. What differed was 49 pixels of 175142
	// (0.03%) in one 6x9 block on the rim, where a multisampled buffer gets
	// resolved. Byte-exactness of a software-rasterised composite is not
	// something the product promises, so the assertion was failing on a
	// property nobody implements.
	//
	// The tolerance is not a guess, it is the gap between two measurements.
	// An orb that is actually animating differs by 67% of its pixels over a
	// SINGLE 16ms frame — 117984 of them, the smallest real animation this can
	// be asked to catch. The resolve noise is 49. The threshold sits at 1%,
	// which is 35x the noise and 67x below one frame of movement; there is no
	// value in between that either measurement comes near. A second draw path
	// outside the loop repaints the orb, not a rim tile, so it lands on the
	// far side of that gap with everything else.
	const changed = await pixelsChanged(page, first, second);
	expect(
		changed.fraction,
		`the orb is still animating: ${changed.differing} of ${changed.total} pixels ` +
			`changed (${(changed.fraction * 100).toFixed(3)}%) with no new frame drawn`
	).toBeLessThan(0.01);
});

/**
 * Fraction of pixels that differ between two PNGs.
 *
 * Decoded in the page rather than by a node PNG library, so this costs no
 * dependency: the browser under test already has `createImageBitmap` and a 2D
 * context, and the images came from it in the first place.
 */
async function pixelsChanged(
	page: import('@playwright/test').Page,
	a: Buffer,
	b: Buffer
): Promise<{ differing: number; total: number; fraction: number }> {
	const result = await page.evaluate(
		async ([aB64, bB64]) => {
			const load = async (b64: string) => {
				const bin = atob(b64);
				const bytes = new Uint8Array(bin.length);
				for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
				const bmp = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
				const canvas = new OffscreenCanvas(bmp.width, bmp.height);
				const ctx = canvas.getContext('2d');
				if (!ctx) throw new Error('no 2d context to decode the screenshots with');
				ctx.drawImage(bmp, 0, 0);
				return { data: ctx.getImageData(0, 0, bmp.width, bmp.height).data, w: bmp.width, h: bmp.height };
			};
			const A = await load(aB64);
			const B = await load(bB64);
			// Different sizes means the canvas resized under us, which is a real
			// failure and not something to average away.
			if (A.w !== B.w || A.h !== B.h) return { differing: -1, total: 0 };
			let differing = 0;
			for (let i = 0; i < A.data.length; i += 4) {
				if (
					A.data[i] !== B.data[i] ||
					A.data[i + 1] !== B.data[i + 1] ||
					A.data[i + 2] !== B.data[i + 2] ||
					A.data[i + 3] !== B.data[i + 3]
				) {
					differing++;
				}
			}
			return { differing, total: A.w * A.h };
		},
		[a.toString('base64'), b.toString('base64')] as [string, string]
	);

	expect(result.differing, 'the orb canvas changed size between the two captures').not.toBe(-1);
	return { ...result, fraction: result.differing / result.total };
}

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

	// Measured here too, and for a reason that only became visible once the
	// noise floor was known. This was `Buffer.compare(first, second) !== 0` —
	// satisfied by a SINGLE differing byte. The rim's multisample resolve
	// supplies about 49 of them for free, so a shader that faithfully drew a
	// frame every tick while rendering an identical picture — uTime unwired,
	// the phases never integrated — would pass both assertions above and be
	// reported as animating while visibly frozen. The frame counter cannot see
	// that: it counts draws, not movement.
	//
	// The threshold is set from the adversarial case, not the quiet one. Pinning
	// every uniform while leaving the loop running — draws counted, picture
	// identical — still moves 3.1% of the pixels, because a live 60fps composite
	// dithers where a paused one does not. A real animation moves 62-79% over
	// this window and never less than 67% over a single frame. 10% sits in that
	// gap with 20x clearance below and 6x above; the 0.03% floor the paused orb
	// shows is not the number to size this against.
	const changed = await pixelsChanged(page, first, second);
	expect(
		changed.fraction,
		`the orb is drawing frames but not moving: only ${changed.differing} of ` +
			`${changed.total} pixels changed (${(changed.fraction * 100).toFixed(3)}%), ` +
			'which is the rim resolving, not an animation'
	).toBeGreaterThan(0.1);
});
