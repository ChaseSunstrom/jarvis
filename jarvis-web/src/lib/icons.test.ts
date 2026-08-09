import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
	APPLE_TOUCH_SIZE,
	ART,
	ICO_SIZES,
	PALETTE,
	PNG_MAGIC,
	artFor,
	buildIcons,
	encodePng,
	rasterize,
	readIco,
	readPng,
	renderSvg
	// @ts-expect-error -- plain .mjs, no types; it is a build script, not app code.
} from '../../scripts/icons.mjs';

const file = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)));
const text = (rel: string) => file(rel).toString('utf8');

const appHtml = text('../app.html');
const svg = file('../../static/favicon.svg');
const ico = file('../../static/favicon.ico');
const appleTouch = file('../../static/apple-touch-icon.png');

/**
 * The committed rasters are compared to a fresh render pixel by pixel, not byte
 * by byte: `deflateSync` output and `Math.cos` are both allowed to change
 * between V8 releases, and neither would mean the icon changed. A ±1 channel
 * tolerance absorbs a last-ulp difference in an antialiased edge; anything that
 * actually altered the art — a radius, a colour, a missing ring — moves whole
 * regions by tens of levels and fails on the max, the mean and the exact count
 * all at once.
 */
function expectSamePixels(actual: Uint8Array, expected: Uint8Array, what: string) {
	expect(actual.length, `${what}: pixel count`).toBe(expected.length);
	let maxDelta = 0;
	let exact = 0;
	for (let i = 0; i < expected.length; i++) {
		const d = Math.abs(actual[i] - expected[i]);
		if (d === 0) exact++;
		else if (d > maxDelta) maxDelta = d;
	}
	expect(maxDelta, `${what}: max channel delta`).toBeLessThanOrEqual(1);
	expect(exact / expected.length, `${what}: fraction of exactly-equal channels`).toBeGreaterThan(
		0.99
	);
}

describe('the generated tab icon', () => {
	it('matches its description — regenerate with `npm run icons`', () => {
		const built = buildIcons() as Array<{ name: string; data: Buffer | string }>;
		expect(built.map((f) => f.name)).toEqual([
			'favicon.svg',
			'favicon.ico',
			'apple-touch-icon.png'
		]);

		// The SVG is text, so it is compared exactly: nothing about emitting it
		// depends on the engine.
		expect(svg.toString('utf8')).toBe(renderSvg());

		const png = readPng(appleTouch);
		expect(png.width).toBe(APPLE_TOUCH_SIZE);
		expect(png.height).toBe(APPLE_TOUCH_SIZE);
		expectSamePixels(
			png.rgba,
			rasterize(APPLE_TOUCH_SIZE, { plateRadius: 0 }),
			'apple-touch-icon.png'
		);

		const entries = readIco(ico) as Array<{ size: number; bpp: number; data: Buffer }>;
		expect(entries.map((e) => e.size)).toEqual(ICO_SIZES);
		for (const entry of entries) {
			expect(entry.bpp).toBe(32);
			expect(entry.data.subarray(0, 8).equals(PNG_MAGIC)).toBe(true);
			const decoded = readPng(entry.data);
			expect(decoded.width).toBe(entry.size);
			expect(decoded.height).toBe(entry.size);
			expectSamePixels(decoded.rgba, rasterize(entry.size), `favicon.ico @${entry.size}`);
		}
	});

	it('uses the design tokens rather than a hex value that looks close', () => {
		const tokens = text('./styles/tokens.css');
		const token = (name: string) => {
			const m = tokens.match(new RegExp(`^\\t--${name}: (.+);$`, 'm'));
			if (!m) throw new Error(`--${name} is not in tokens.css`);
			return m[1].trim().toLowerCase();
		};
		expect(PALETTE.bg).toBe(token('jv-bg'));
		expect(PALETTE.accent).toBe(token('jv-accent'));
	});

	it('drops the detail that cannot survive a 16 px tab strip', () => {
		const small = artFor(16) as Array<{ kind: string }>;
		const large = artFor(180) as Array<{ kind: string }>;

		// No arcs and no ticks at 16 px: at four canvas units to the pixel, the
		// 10-degree gaps and the 1.5-unit ticks are both sub-pixel.
		expect(small.some((p) => p.kind === 'arc' || p.kind === 'tick')).toBe(false);
		expect(large.filter((p) => p.kind === 'arc')).toHaveLength(3);
		expect(large.filter((p) => p.kind === 'tick')).toHaveLength(8);

		// Both keep the silhouette: dark plate, a ring, a hot core.
		for (const [label, art] of [
			['16', small],
			['180', large]
		] as const) {
			expect(art.filter((p) => p.kind === 'plate'), label).toHaveLength(1);
			expect(art.some((p) => p.kind === 'ring'), label).toBe(true);
			expect(art.filter((p) => p.kind === 'disc').length, label).toBeGreaterThanOrEqual(3);
		}

		// And every primitive is reachable at some size — a minPx/maxPx pair that
		// excludes everything would silently drop art from every output.
		for (const p of ART as Array<{ minPx?: number; maxPx?: number }>) {
			expect(p.minPx ?? 0).toBeLessThan(p.maxPx ?? Number.POSITIVE_INFINITY);
		}
	});

	it('renders a lit core on a dark plate at every size it ships at', () => {
		// The failure this guards against is a favicon that is technically valid
		// and visually a dark square: every ring drawn sub-pixel, nothing left.
		for (const size of [...ICO_SIZES, APPLE_TOUCH_SIZE]) {
			const px = rasterize(size);
			const at = (x: number, y: number) => {
				const i = (Math.floor(y) * size + Math.floor(x)) * 4;
				return [px[i], px[i + 1], px[i + 2], px[i + 3]];
			};
			const centre = at(size / 2, size / 2);
			expect(centre[3], `${size}: centre is opaque`).toBe(255);
			expect(Math.min(centre[0], centre[1], centre[2]), `${size}: centre is lit`).toBeGreaterThan(
				200
			);

			// A ring's worth of cyan between the core and the edge: sample the
			// midpoint of the radius along +x, where every size has ring or plate.
			const edge = at(size - 1, size / 2);
			expect(
				Math.max(edge[0], edge[1], edge[2]),
				`${size}: the plate stays dark at the edge`
			).toBeLessThan(90);

			// Blue dominates: this is a cyan icon, not a grey one.
			let blue = 0;
			let red = 0;
			for (let i = 0; i < px.length; i += 4) {
				red += px[i] * px[i + 3];
				blue += px[i + 2] * px[i + 3];
			}
			expect(blue, `${size}: blue-dominant`).toBeGreaterThan(red * 1.5);
		}
	});

	it('round-trips its own PNG encoder', () => {
		// readPng is only used by this file, so it needs its own proof: a decoder
		// that silently returned the buffer it was handed would make every
		// comparison above vacuous.
		const rgba = new Uint8Array([1, 2, 3, 4, 250, 251, 252, 253, 10, 20, 30, 40, 99, 98, 97, 96]);
		const decoded = readPng(encodePng(2, 2, rgba));
		expect(decoded.width).toBe(2);
		expect(decoded.height).toBe(2);
		expect([...decoded.rgba]).toEqual([...rgba]);
		expect(() => readPng(Buffer.from('not a png at all, really'))).toThrow(/not a PNG/);
	});
});

describe('app.html', () => {
	it('links every generated icon', () => {
		expect(appHtml).toContain(
			'<link rel="icon" href="%sveltekit.assets%/favicon.ico" sizes="32x32" />'
		);
		expect(appHtml).toContain(
			'<link rel="icon" href="%sveltekit.assets%/favicon.svg" type="image/svg+xml" />'
		);
		expect(appHtml).toContain(
			'<link rel="apple-touch-icon" href="%sveltekit.assets%/apple-touch-icon.png" />'
		);

		// The blank placeholder that used to be here suppressed the request
		// entirely; leaving it in alongside the real ones would win by being first.
		expect(appHtml).not.toContain('href="data:,"');
	});

	it('offers the vector after the raster, so browsers that can take it do', () => {
		expect(appHtml.indexOf('favicon.ico')).toBeLessThan(appHtml.indexOf('favicon.svg'));
	});
});
