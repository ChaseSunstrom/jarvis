/**
 * The Jarvis arc reactor, as data — the browser-tab icon.
 *
 * Everything the favicon is made of lives in [ART] below: rings, ticks, arcs
 * and discs in a 64-unit square. From that one description this module emits
 * both the vector form (`favicon.svg`) and the raster forms (`favicon.ico`,
 * `apple-touch-icon.png`), so the two can never drift apart the way a
 * hand-drawn SVG and a hand-exported PNG always eventually do.
 *
 * Why a rasteriser and not an image library: the shapes are circles, rings,
 * line segments and a rounded rectangle, all of which are one distance
 * function each, and a favicon is 16 px across. A 8x8 supersampled evaluation
 * of those distance functions is ~80 lines and needs nothing from npm, which
 * matters for a package whose whole dependency list is `ws`.
 *
 * The colours are the design tokens, not approximations of them:
 * `src/lib/icons.test.ts` reads `src/lib/styles/tokens.css` and fails if
 * [PALETTE] stops matching `--jv-bg` / `--jv-accent`.
 *
 * Regenerate with `npm run icons`; `npm run icons -- --check` verifies the
 * committed files without writing.
 */

import { readFileSync } from 'node:fs';
import { deflateSync, inflateSync } from 'node:zlib';

/** The art is authored in a 64x64 box; every radius below is in those units. */
export const CANVAS = 64;
const C = CANVAS / 2;

/** Corner radius of the backing plate, in canvas units. */
export const PLATE_RADIUS = 13;

// The two colours the icon needs, read from the generated tokens rather than
// typed here: design/tokens.json is the only place a colour is typed, and the
// favicon drifting from the accent is exactly the kind of near-miss nobody sees.
const _tokensCss = readFileSync(new URL('../src/lib/styles/tokens.css', import.meta.url), 'utf8');
const _token = (name) => {
	const found = _tokensCss.match(new RegExp(`^\\t--${name}: (.+);$`, 'm'));
	if (!found) throw new Error(`--${name} is not in tokens.css`);
	return found[1].trim().toLowerCase();
};
export const PALETTE = {
	/** --jv-bg: the plate, so the reactor reads on a light tab strip too. */
	bg: _token('jv-bg'),
	/** --jv-accent: rings, ticks, arcs, glow. */
	accent: _token('jv-accent'),
	/** --jv-accent-lift: the inner rim, the lit edge one step brighter than the accent. */
	rim: _token('jv-accent-lift'),
	/** The core and the hot centre: the icon's own two highlights, brighter than
	 * any text token because they are light, not text. Icon-only by design. */
	core: '#aef2ff',
	hot: '#ffffff'
};

/**
 * Detail is a function of how many pixels the thing will actually occupy.
 *
 * A 64-unit canvas drawn at 16 px is four canvas units per pixel, so the
 * 1.6-unit gauge ring is four tenths of a pixel wide and the 10-degree gaps in
 * the dashed ring are a third of one. Rendering the full reactor at that size
 * does not produce a small reactor, it produces a grey smudge.
 *
 * So each primitive declares the size range it survives — `minPx` inclusive,
 * `maxPx` exclusive — and the small sizes get purpose-drawn stand-ins with the
 * same silhouette (dark plate, cyan ring, hot core) instead of a downsample of
 * detail that was never legible.
 */
const CARDINAL = [0, 90, 180, 270];
const DIAGONAL = [45, 135, 225, 315];

/** Below this, the simplified reactor. */
const FINE = 24;
/** Below this, no dashed mid ring — the gaps close up. */
const DASH = 30;
/** Below this, no minor ticks. */
const MINOR = 40;

/** The reactor, outermost first. */
export const ART = [
	{ kind: 'plate', color: 'bg' },
	{ kind: 'glow', r: 30, color: 'accent', alpha: 0.22 },

	// --- the simplified reactor, for tab-strip sizes -------------------------
	{ kind: 'ring', r: 24, w: 4.5, color: 'accent', alpha: 0.9, maxPx: FINE },
	{ kind: 'disc', r: 12, color: 'accent', alpha: 0.4, maxPx: FINE },
	{ kind: 'disc', r: 8.5, color: 'core', alpha: 1, maxPx: FINE },
	{ kind: 'disc', r: 5, color: 'hot', alpha: 1, maxPx: FINE },

	// --- the full reactor ----------------------------------------------------
	// outer gauge ring
	{ kind: 'ring', r: 25, w: 2.2, color: 'accent', alpha: 0.7, minPx: FINE },

	// major ticks, crossing the gauge ring
	...CARDINAL.map((a) => ({
		kind: 'tick',
		angle: a,
		r0: 21.5,
		r1: 29,
		w: 2.4,
		color: 'accent',
		alpha: 0.95,
		minPx: FINE
	})),
	// minor ticks
	...DIAGONAL.map((a) => ({
		kind: 'tick',
		angle: a,
		r0: 23.5,
		r1: 27.5,
		w: 1.8,
		color: 'accent',
		alpha: 0.6,
		minPx: MINOR
	})),

	// the dashed mid ring: three 110-degree arcs with 10-degree gaps
	...[-85, 35, 155].map((from) => ({
		kind: 'arc',
		r: 19,
		w: 3,
		from,
		to: from + 110,
		color: 'accent',
		alpha: 0.9,
		minPx: DASH
	})),

	// inner rim
	{ kind: 'ring', r: 12, w: 2.2, color: 'rim', alpha: 1, minPx: FINE },

	// core: halo, body, hot centre
	{ kind: 'disc', r: 9.5, color: 'accent', alpha: 0.35, minPx: FINE },
	{ kind: 'disc', r: 7, color: 'core', alpha: 1, minPx: FINE },
	{ kind: 'disc', r: 4.5, color: 'hot', alpha: 1, minPx: FINE }
];

/** The primitives drawn at a given output size. */
export function artFor(sizePx) {
	return ART.filter(
		(p) => sizePx >= (p.minPx ?? 0) && sizePx < (p.maxPx ?? Number.POSITIVE_INFINITY)
	);
}

// --- geometry ---------------------------------------------------------------

const RAD = Math.PI / 180;

/**
 * A point on a circle of radius [r] about the centre.
 *
 * Screen convention: y grows downwards and the angle turns clockwise, which is
 * SVG's convention, so the rasteriser and the SVG emitter agree by
 * construction rather than by a sign that has to be remembered twice.
 */
function polar(r, deg) {
	return [C + r * Math.cos(deg * RAD), C + r * Math.sin(deg * RAD)];
}

/** Angular width of `from -> to`, normalised into [0, 360). */
function span(from, to) {
	return (((to - from) % 360) + 360) % 360;
}

// --- rasteriser -------------------------------------------------------------

/**
 * Supersampling factor: 8x8 (64 antialiasing levels) where a single pixel
 * carries a whole ring, 4x4 above 64 px where the shapes are many pixels wide
 * and 16 levels are already invisible. Derived from the size alone, so a given
 * size always renders identically.
 */
const ss = (sizePx) => (sizePx >= 64 ? 4 : 8);

function hexToRgb(hex) {
	const v = parseInt(hex.slice(1), 16);
	return [(v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff];
}

/** Straight-alpha coverage of one primitive at canvas-space point (x, y). */
function coverage(p, x, y, plateRadius) {
	const dx = x - C;
	const dy = y - C;
	// Math.sqrt is exactly specified by IEEE 754; Math.hypot is not, and this
	// runs on whatever V8 the machine happens to have.
	const dist = Math.sqrt(dx * dx + dy * dy);

	switch (p.kind) {
		case 'plate': {
			const half = C;
			const rr = plateRadius;
			const qx = Math.abs(dx) - (half - rr);
			const qy = Math.abs(dy) - (half - rr);
			const ax = Math.max(qx, 0);
			const ay = Math.max(qy, 0);
			const sd = Math.sqrt(ax * ax + ay * ay) + Math.min(Math.max(qx, qy), 0) - rr;
			return sd <= 0 ? 1 : 0;
		}
		case 'glow': {
			// Linear falloff, matching the two-stop radial gradient in the SVG.
			if (dist >= p.r) return 0;
			return 1 - dist / p.r;
		}
		case 'disc':
			return dist <= p.r ? 1 : 0;
		case 'ring':
			return Math.abs(dist - p.r) <= p.w / 2 ? 1 : 0;
		case 'arc': {
			if (Math.abs(dist - p.r) > p.w / 2) return 0;
			const ang = Math.atan2(dy, dx) / RAD;
			// Butt caps, like the SVG's default stroke-linecap.
			return span(p.from, ang) <= span(p.from, p.to) ? 1 : 0;
		}
		case 'tick': {
			const [x0, y0] = polar(p.r0, p.angle);
			const [x1, y1] = polar(p.r1, p.angle);
			const vx = x1 - x0;
			const vy = y1 - y0;
			const len2 = vx * vx + vy * vy;
			let t = ((x - x0) * vx + (y - y0) * vy) / len2;
			t = t < 0 ? 0 : t > 1 ? 1 : t;
			const px = x - (x0 + t * vx);
			const py = y - (y0 + t * vy);
			return Math.sqrt(px * px + py * py) <= p.w / 2 ? 1 : 0;
		}
		default:
			throw new Error(`unknown primitive: ${p.kind}`);
	}
}

/**
 * Draw the reactor at [sizePx] square.
 *
 * @param {number} sizePx
 * @param {{plateRadius?: number}} [opts] plateRadius 0 gives a full-bleed
 *   square, which is what iOS wants for an apple-touch-icon because it applies
 *   its own mask and a pre-rounded icon ends up rounded twice.
 * @returns {Uint8Array} straight-alpha RGBA8, row major, `sizePx * sizePx * 4`.
 */
export function rasterize(sizePx, opts = {}) {
	const plateRadius = opts.plateRadius ?? PLATE_RADIUS;
	const art = artFor(sizePx).map((p) => ({ ...p, rgb: hexToRgb(PALETTE[p.color]) }));
	const out = new Uint8Array(sizePx * sizePx * 4);
	const scale = CANVAS / sizePx;
	const SS = ss(sizePx);
	const samples = SS * SS;

	for (let py = 0; py < sizePx; py++) {
		for (let px = 0; px < sizePx; px++) {
			// Accumulate premultiplied colour so partial coverage composites the
			// same way a renderer would, then unpremultiply once at the end.
			let ar = 0;
			let ag = 0;
			let ab = 0;
			let aa = 0;

			for (let sy = 0; sy < SS; sy++) {
				for (let sx = 0; sx < SS; sx++) {
					const x = (px + (sx + 0.5) / SS) * scale;
					const y = (py + (sy + 0.5) / SS) * scale;
					let r = 0;
					let g = 0;
					let b = 0;
					let a = 0;
					for (const p of art) {
						const cov = coverage(p, x, y, plateRadius);
						if (cov === 0) continue;
						const sa = cov * (p.alpha ?? 1);
						// src-over, straight alpha
						const na = sa + a * (1 - sa);
						if (na === 0) continue;
						r = (p.rgb[0] * sa + r * a * (1 - sa)) / na;
						g = (p.rgb[1] * sa + g * a * (1 - sa)) / na;
						b = (p.rgb[2] * sa + b * a * (1 - sa)) / na;
						a = na;
					}
					ar += r * a;
					ag += g * a;
					ab += b * a;
					aa += a;
				}
			}

			const alpha = aa / samples;
			const i = (py * sizePx + px) * 4;
			if (alpha > 0) {
				out[i] = Math.round(Math.min(255, ar / aa));
				out[i + 1] = Math.round(Math.min(255, ag / aa));
				out[i + 2] = Math.round(Math.min(255, ab / aa));
			}
			out[i + 3] = Math.round(alpha * 255);
		}
	}
	return out;
}

// --- PNG --------------------------------------------------------------------

const CRC_TABLE = (() => {
	const t = new Uint32Array(256);
	for (let n = 0; n < 256; n++) {
		let c = n;
		for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
		t[n] = c >>> 0;
	}
	return t;
})();

function crc32(buf) {
	let c = 0xffffffff;
	for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
	return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
	const len = Buffer.alloc(4);
	len.writeUInt32BE(data.length, 0);
	const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
	const crc = Buffer.alloc(4);
	crc.writeUInt32BE(crc32(body), 0);
	return Buffer.concat([len, body, crc]);
}

export const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/**
 * Minimal RGBA8 PNG. Every scanline uses filter 0 (None), which costs a few
 * bytes and buys a decoder — see `readPng` — that is 20 lines instead of 80.
 */
export function encodePng(width, height, rgba) {
	const ihdr = Buffer.alloc(13);
	ihdr.writeUInt32BE(width, 0);
	ihdr.writeUInt32BE(height, 4);
	ihdr[8] = 8; // bit depth
	ihdr[9] = 6; // colour type: RGBA
	ihdr[10] = 0; // deflate
	ihdr[11] = 0; // adaptive filtering
	ihdr[12] = 0; // no interlace

	const stride = width * 4;
	const raw = Buffer.alloc((stride + 1) * height);
	for (let y = 0; y < height; y++) {
		raw[y * (stride + 1)] = 0;
		Buffer.from(rgba.buffer, rgba.byteOffset + y * stride, stride).copy(
			raw,
			y * (stride + 1) + 1
		);
	}

	return Buffer.concat([
		PNG_MAGIC,
		pngChunk('IHDR', ihdr),
		pngChunk('IDAT', deflateSync(raw, { level: 9 })),
		pngChunk('IEND', Buffer.alloc(0))
	]);
}

/**
 * The inverse of [encodePng], for tests: pull the pixels back out of a
 * committed file so they can be compared with a fresh render.
 *
 * Only understands what [encodePng] writes (RGBA8, filter 0, one IDAT run) and
 * says so loudly otherwise, rather than silently returning something plausible.
 */
export function readPng(buf) {
	if (!buf.subarray(0, 8).equals(PNG_MAGIC)) throw new Error('not a PNG');
	let off = 8;
	let width = 0;
	let height = 0;
	const idat = [];
	while (off < buf.length) {
		const len = buf.readUInt32BE(off);
		const type = buf.subarray(off + 4, off + 8).toString('ascii');
		const data = buf.subarray(off + 8, off + 8 + len);
		if (type === 'IHDR') {
			width = data.readUInt32BE(0);
			height = data.readUInt32BE(4);
			if (data[8] !== 8 || data[9] !== 6 || data[12] !== 0) {
				throw new Error('unexpected PNG format: want 8-bit RGBA, non-interlaced');
			}
		} else if (type === 'IDAT') {
			idat.push(data);
		}
		off += 12 + len;
	}
	const raw = inflateSync(Buffer.concat(idat));
	const stride = width * 4;
	const rgba = new Uint8Array(stride * height);
	for (let y = 0; y < height; y++) {
		const filter = raw[y * (stride + 1)];
		if (filter !== 0) throw new Error(`unexpected PNG filter ${filter}; encodePng only writes 0`);
		rgba.set(raw.subarray(y * (stride + 1) + 1, y * (stride + 1) + 1 + stride), y * stride);
	}
	return { width, height, rgba };
}

// --- ICO --------------------------------------------------------------------

/**
 * A PNG-in-ICO container: the ICONDIR header plus one entry per size, each
 * pointing at a whole PNG file. Understood by every browser since IE11, and
 * far smaller than the BMP-with-AND-mask form.
 */
export function encodeIco(pngs) {
	const header = Buffer.alloc(6);
	header.writeUInt16LE(0, 0); // reserved
	header.writeUInt16LE(1, 2); // type: icon
	header.writeUInt16LE(pngs.length, 4);

	const dir = Buffer.alloc(16 * pngs.length);
	let offset = header.length + dir.length;
	pngs.forEach(({ size, data }, i) => {
		const at = i * 16;
		dir[at] = size >= 256 ? 0 : size;
		dir[at + 1] = size >= 256 ? 0 : size;
		dir[at + 2] = 0; // palette size
		dir[at + 3] = 0; // reserved
		dir.writeUInt16LE(1, at + 4); // colour planes
		dir.writeUInt16LE(32, at + 6); // bits per pixel
		dir.writeUInt32LE(data.length, at + 8);
		dir.writeUInt32LE(offset, at + 12);
		offset += data.length;
	});

	return Buffer.concat([header, dir, ...pngs.map((p) => p.data)]);
}

/** The inverse of [encodeIco], for tests. */
export function readIco(buf) {
	if (buf.readUInt16LE(0) !== 0 || buf.readUInt16LE(2) !== 1) throw new Error('not an ICO');
	const count = buf.readUInt16LE(4);
	const entries = [];
	for (let i = 0; i < count; i++) {
		const at = 6 + i * 16;
		entries.push({
			size: buf[at] === 0 ? 256 : buf[at],
			bpp: buf.readUInt16LE(at + 6),
			data: buf.subarray(buf.readUInt32LE(at + 12), buf.readUInt32LE(at + 12) + buf.readUInt32LE(at + 8))
		});
	}
	return entries;
}

// --- SVG --------------------------------------------------------------------

/** Trim float noise so the emitted SVG is byte-stable. */
function n(v) {
	return String(Math.round(v * 1000) / 1000);
}

function arcPath(p) {
	const [x0, y0] = polar(p.r, p.from);
	const [x1, y1] = polar(p.r, p.to);
	const large = span(p.from, p.to) > 180 ? 1 : 0;
	return `M${n(x0)} ${n(y0)}A${n(p.r)} ${n(p.r)} 0 ${large} 1 ${n(x1)} ${n(y1)}`;
}

/**
 * The same reactor as [rasterize], as SVG.
 *
 * Vector is the primary favicon: browsers that support `image/svg+xml` render
 * it crisply at whatever the tab strip and the display's scale factor work out
 * to, which no fixed-size PNG can do.
 */
export function renderSvg(opts = {}) {
	const plateRadius = opts.plateRadius ?? PLATE_RADIUS;
	const art = artFor(CANVAS); // vector always gets the fine detail
	const body = [];

	for (const p of art) {
		const color = PALETTE[p.color];
		const opacity = p.alpha ?? 1;
		const fadeOpacity = opacity === 1 ? '' : ` opacity="${n(opacity)}"`;
		switch (p.kind) {
			case 'plate':
				body.push(
					`<rect width="${CANVAS}" height="${CANVAS}" rx="${n(plateRadius)}" fill="${color}"/>`
				);
				break;
			case 'glow':
				body.push(`<circle cx="${C}" cy="${C}" r="${n(p.r)}" fill="url(#jvGlow)"/>`);
				break;
			case 'disc':
				body.push(`<circle cx="${C}" cy="${C}" r="${n(p.r)}" fill="${color}"${fadeOpacity}/>`);
				break;
			case 'ring':
				body.push(
					`<circle cx="${C}" cy="${C}" r="${n(p.r)}" fill="none" stroke="${color}" stroke-width="${n(p.w)}"${fadeOpacity}/>`
				);
				break;
			case 'arc':
				body.push(
					`<path d="${arcPath(p)}" fill="none" stroke="${color}" stroke-width="${n(p.w)}"${fadeOpacity}/>`
				);
				break;
			case 'tick': {
				const [x0, y0] = polar(p.r0, p.angle);
				const [x1, y1] = polar(p.r1, p.angle);
				body.push(
					`<path d="M${n(x0)} ${n(y0)}L${n(x1)} ${n(y1)}" stroke="${color}" stroke-width="${n(p.w)}"${fadeOpacity}/>`
				);
				break;
			}
			default:
				throw new Error(`unknown primitive: ${p.kind}`);
		}
	}

	const glow = ART.find((p) => p.kind === 'glow');
	return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${CANVAS} ${CANVAS}" width="${CANVAS}" height="${CANVAS}" role="img" aria-label="Jarvis">
<title>Jarvis</title>
<defs>
<radialGradient id="jvGlow">
<stop offset="0" stop-color="${PALETTE[glow.color]}" stop-opacity="${n(glow.alpha)}"/>
<stop offset="1" stop-color="${PALETTE[glow.color]}" stop-opacity="0"/>
</radialGradient>
</defs>
${body.join('\n')}
</svg>
`;
}

// --- the files --------------------------------------------------------------

/** Sizes packed into `favicon.ico`: tab strip, retina tab strip, taskbar. */
export const ICO_SIZES = [16, 32, 48];

/** iOS home-screen icon size. */
export const APPLE_TOUCH_SIZE = 180;

/**
 * Every generated file, as `{ name, data }` with `data` a Buffer (binary) or a
 * string (text). Written by `make-icons.mjs`, re-derived by `icons.test.ts`.
 */
export function buildIcons() {
	const ico = encodeIco(
		ICO_SIZES.map((size) => ({ size, data: encodePng(size, size, rasterize(size)) }))
	);
	return [
		{ name: 'favicon.svg', data: renderSvg() },
		{ name: 'favicon.ico', data: ico },
		{
			name: 'apple-touch-icon.png',
			// Square, not rounded: iOS masks it itself.
			data: encodePng(
				APPLE_TOUCH_SIZE,
				APPLE_TOUCH_SIZE,
				rasterize(APPLE_TOUCH_SIZE, { plateRadius: 0 })
			)
		}
	];
}
