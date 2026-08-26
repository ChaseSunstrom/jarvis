// Every console route, opened in a real browser against the real console.
//
// Run as: node browser_routes.cjs '<json job>'  ->  one JSON line on stdout.
//
// The job names the console's URL and the routes; for each route this opens
// the page, waits for its probe (the `data-testid` `screens.ts` says proves
// the screen rendered), records every console error and page error, and
// measures what Reactor II asks of the render — the body face, the ground,
// the panel colours against the tokens, the grid and brackets that must not
// be there. The e2e suite asks the same of the mock-backed build
// (`look.spec.ts`); this asks it of the console people actually open.
const { chromium } = require('@playwright/test');

const job = JSON.parse(process.argv[2] || '{}');
const timeout = job.timeoutMs || 30000;
const say = (payload) => process.stdout.write(JSON.stringify(payload) + '\n');

(async () => {
	const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
	const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
	const page = await context.newPage();
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	const results = [];
	for (const route of job.routes) {
		const errors = [];
		const onConsole = (m) => {
			if (m.type() === 'error') errors.push(`console: ${m.text()}`.slice(0, 300));
		};
		const onError = (e) => errors.push(`pageerror: ${e.message}`.slice(0, 300));
		page.on('console', onConsole);
		page.on('pageerror', onError);
		const entry = { path: route.path, name: route.name, ok: false, errors, facts: null, note: '' };
		try {
			await page.goto(`${job.url}${route.path}`, { waitUntil: 'domcontentloaded', timeout });
			await page.getByTestId(route.probe).waitFor({ state: 'visible', timeout });
			await page.waitForTimeout(500);
			entry.facts = await page.evaluate((tokens) => {
				const lower = (s) => s.toLowerCase();
				const rgb = (h) => {
					const n = parseInt(h.slice(1), 16);
					return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
				};
				const palette = new Set(Object.values(tokens).filter((v) => /^#[0-9a-f]{6}$/i.test(v)).map(rgb));
				const body = getComputedStyle(document.body);
				const visible = (el) => el.offsetParent !== null;
				const panels = [...document.querySelectorAll('section, article, aside, header, footer, div')]
					.filter(visible)
					.map((el) => getComputedStyle(el).backgroundColor)
					.filter((c) => c.startsWith('rgb(') && !palette.has(c));
				const prose = [...document.querySelectorAll('p, li, dd, label, h1, h2, h3')]
					.filter((el) => visible(el) && (el.textContent || '').trim().length > 24)
					.filter((el) => lower(getComputedStyle(el).fontFamily).includes('mono'))
					.filter((el) => !el.closest('pre, code, [data-mono], .calls, .k, dl'))
					.map((el) => (el.textContent || '').trim().slice(0, 40));
				return {
					bodyFont: lower(body.fontFamily),
					ground: body.backgroundColor === rgb(tokens['--jv-bg']),
					grid: document.querySelectorAll('.jv-grid, .jv-bracket').length,
					canvas: document.querySelectorAll('canvas').length,
					offPalette: [...new Set(panels)].slice(0, 5),
					monoProse: prose.slice(0, 5)
				};
			}, job.tokens);
			entry.ok = true;
		} catch (err) {
			entry.note = String((err && err.message) || err).slice(0, 300);
		}
		page.off('console', onConsole);
		page.off('pageerror', onError);
		results.push(entry);
	}
	await browser.close();
	say({ results });
})().catch((err) => {
	say({ error: String((err && err.message) || err) });
	process.exit(1);
});
