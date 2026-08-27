// docs/design/screenshot.mjs — render every mockup headlessly to docs/design/shots/.
//
// Run from the repo root:  node docs/design/screenshot.mjs
// Uses jarvis-web's Playwright (npm ci there first) and its Chromium
// (`npx playwright install chromium`). No display needed. Fonts are the
// woff2 files under docs/design/fonts/, so the render is the same anywhere.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { mkdirSync } from 'node:fs';

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(here, '..', '..', 'jarvis-web', 'package.json'));
let chromium;
try { ({ chromium } = require('@playwright/test')); } catch { ({ chromium } = require('playwright-core')); }

const DIRECTIONS = ['a-instrument', 'b-ledger', 'c-reactor'];
const SCREENS = ['chat', 'task', 'dashboard'];
const out = join(here, 'shots');
mkdirSync(out, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({
	viewport: { width: 1440, height: 900 },
	deviceScaleFactor: 1,
	colorScheme: 'dark',
	reducedMotion: 'reduce'
});
const settle = async (page) => {
	await page.evaluate(() => document.fonts.ready);
	await page.waitForTimeout(250);
};
for (const d of DIRECTIONS) {
	for (const s of SCREENS) {
		const name = `${d}-${s}`;
		const page = await ctx.newPage();
		await page.goto(`file://${join(here, name + '.html')}`);
		await settle(page);
		await page.screenshot({ path: join(out, name + '.png') });
		await page.close();
		console.log('shot', name);
	}
}
// The contact sheet, whole and one strip per direction.
for (const [suffix, query] of [['contact-sheet', ''], ...DIRECTIONS.map((d) => [`strip-${d}`, `?only=${d}`])]) {
	const page = await ctx.newPage();
	await page.setViewportSize({ width: 1600, height: 900 });
	await page.goto(`file://${join(here, 'index.html')}${query}`);
	await settle(page);
	await page.screenshot({ path: join(out, suffix + '.png'), fullPage: true });
	await page.close();
	console.log('shot', suffix);
}
await browser.close();
