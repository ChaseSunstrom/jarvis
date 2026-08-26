// One turn through the real console, in a real browser.
//
// Run as: node browser_turn.cjs '<json job>'  ->  one JSON line on stdout.
//
// Why Node and not python-playwright: `jarvis-web/node_modules` already has
// Playwright and its Chromium. A second copy for Python would be 150 MB and a
// second version to keep in step, for the same browser doing the same job.
//
// Two modes:
//   voice — Chromium's fake capture device is pointed at a WAV, so the page's
//           OWN VAD hears speech, opens its OWN websocket and streams its OWN
//           audio. Nothing is stubbed: `?e2e=1` (which forces a turn to start
//           because the mock backend has no audio) is deliberately NOT used.
//   text  — the chat panel, typed into, which is that surface's real entry point.
// Resolved from jarvis-web's node_modules by path, not by cwd: this file lives
// outside that tree, so a bare require() finds nothing wherever it is run from.
const { chromium } = require(require.resolve('@playwright/test', {
	paths: [require('path').join(__dirname, '..', '..', 'jarvis-web')],
}));

const job = JSON.parse(process.argv[2] || '{}');
const timeout = job.timeoutMs || 180000;

const say = (payload) => process.stdout.write(JSON.stringify(payload) + '\n');

(async () => {
	const args = [
		'--no-sandbox',
		'--use-fake-ui-for-media-stream',
		'--use-fake-device-for-media-stream',
		'--autoplay-policy=no-user-gesture-required'
	];
	if (job.mode === 'voice' && job.wav) {
		// %noloop matters: looped audio makes the VAD hear a second utterance
		// while the first is still being answered, and the run never settles.
		args.push(`--use-file-for-fake-audio-capture=${job.wav}%noloop`);
	}

	const browser = await chromium.launch({ headless: job.headless !== false, args });
	const context = await browser.newContext({ permissions: ['microphone'] });
	const page = await context.newPage();

	const marks = {};
	const mark = (name) => {
		if (marks[name] === undefined) marks[name] = Date.now() - t0;
	};
	let ttsUrl = '';
	page.on('response', (r) => {
		const url = r.url();
		if (url.includes('/api/tts_proxy/')) {
			ttsUrl = url;
			mark('tts');
		}
	});
	const logs = [];
	page.on('console', (m) => logs.push(`${m.type()}: ${m.text()}`.slice(0, 300)));
	page.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`.slice(0, 300)));

	const t0 = Date.now();
	try {
		await page.goto(job.mode === 'text' ? `${job.url}/?mode=chat` : job.url, {
			waitUntil: 'domcontentloaded',
			timeout
		});

		if (job.mode === 'text') {
			const input = page.getByTestId('chat-input');
			await input.waitFor({ state: 'visible', timeout });
			await input.fill(job.text);
			await page.getByTestId('chat-send').click();
		} else {
			// The page opens the microphone on mount; the fake device starts
			// playing the file at the same moment, and the WAV's lead-in silence
			// is what keeps the first syllable from being lost to that race.
			await page.getByTestId('mic').waitFor({ state: 'visible', timeout });
		}

		// The HUD shows one transcript and one response; chat mode shows a list,
		// where the answer is the LAST assistant bubble.
		const transcriptBox = page.getByTestId('transcript');
		const responseBox =
			job.mode === 'text'
				? page.getByTestId('chat-text').last()
				: page.getByTestId('response');

		// Non-empty, not merely present: both boxes exist from the first paint.
		const settled = async (locator) => {
			const text = (await locator.textContent().catch(() => '')) || '';
			return text.trim();
		};
		const deadline = Date.now() + timeout;
		let transcript = '';
		let response = '';
		while (Date.now() < deadline) {
			// In chat mode what was "heard" is what was typed: there is no STT in
			// that surface, and claiming a transcript would put a number in the
			// WER column for a path that never recognised anything.
			transcript = transcript || (job.mode === 'text' ? job.text : await settled(transcriptBox));
			if (transcript) mark('stt');
			response = await settled(responseBox);
			if (response) {
				mark('response');
				// Let a streaming answer finish: two identical reads 700 ms apart.
				await page.waitForTimeout(700);
				const again = await settled(responseBox);
				if (again === response) break;
				response = again;
			}
			await page.waitForTimeout(200);
		}

		const latencyLabel = await settled(page.getByTestId('latency'));

		// What the page shows *after* the answer — the task dock filling, a
		// step count rising. Each probe waits for its text, bounded, and
		// reports what was actually there when it gave up, so a failure names
		// the real state of the page and not just "not found".
		const probes = [];
		for (const probe of job.probes || []) {
			const until = Date.now() + (probe.withinMs || 30000);
			let text = '';
			let ok = false;
			while (Date.now() < until) {
				text = await settled(page.getByTestId(probe.testid).first());
				if (text.toLowerCase().includes(String(probe.contains || '').toLowerCase())) {
					ok = true;
					break;
				}
				await page.waitForTimeout(500);
			}
			probes.push({ testid: probe.testid, contains: probe.contains, ok, text: text.slice(0, 200) });
		}
		say({
			transcript,
			response,
			ttsUrl,
			latency: marks,
			latencyLabel,
			probes,
			logs: logs.slice(-20),
			error: response ? '' : 'no answer appeared before the timeout'
		});
	} catch (err) {
		say({ error: String((err && err.message) || err), logs: logs.slice(-20) });
	} finally {
		await browser.close();
	}
})();
