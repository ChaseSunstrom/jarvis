// docs/design/screenshot-c2.mjs — render the Reactor II mockup: three views as
// PNGs (mid-animation, 1.6 s in) and two short WebM clips of the motion.
//   node docs/design/screenshot-c2.mjs
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { mkdirSync, renameSync, readdirSync } from 'node:fs';
const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(here, '..', '..', 'jarvis-web', 'package.json'));
let chromium; try { ({ chromium } = require('@playwright/test')); } catch { ({ chromium } = require('playwright-core')); }
const out = join(here, 'shots'); mkdirSync(out, { recursive: true });
const url = (view) => `file://${join(here, 'c2-reactor.html')}?view=${view}`;
const browser = await chromium.launch();
// stills, animations running
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark', reducedMotion: 'no-preference' });
for (const view of ['chat', 'task', 'dashboard']) {
	const page = await ctx.newPage();
	await page.goto(url(view));
	await page.evaluate(() => document.fonts.ready);
	await page.waitForTimeout(1600);
	await page.screenshot({ path: join(out, `c2-reactor-${view}.png`) });
	await page.close();
	console.log('shot', view);
}
await ctx.close();
// clips
for (const view of ['chat', 'task']) {
	const vdir = join(out, 'video-' + view);
	const vctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark', reducedMotion: 'no-preference', recordVideo: { dir: vdir, size: { width: 1440, height: 900 } } });
	const page = await vctx.newPage();
	await page.goto(url(view));
	await page.evaluate(() => document.fonts.ready);
	await page.waitForTimeout(7000);
	await page.close();
	await vctx.close();
	const f = readdirSync(vdir).find((n) => n.endsWith('.webm'));
	renameSync(join(vdir, f), join(out, `c2-reactor-${view}.webm`));
	console.log('clip', view);
}
await browser.close();
