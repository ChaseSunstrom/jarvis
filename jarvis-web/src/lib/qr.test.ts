import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';

import {
	MAX_VERSION,
	MIN_VERSION,
	QrError,
	dataCodewords,
	encodeQr,
	functionModules,
	qrSvg,
	rawDataModules,
	type EccLevel
} from './qr';

/**
 * How this file earns the right to be short.
 *
 * A QR encoder cannot be checked by eye: a wrong generator polynomial, an
 * off-by-one in the block interleave or a transposed format field all produce
 * output that still looks exactly like a QR code — square, three finders,
 * plausible noise — and still fails in a phone camera. The first version of
 * this encoder had its format bits written at [x][y] instead of [y][x]; every
 * symbol it produced was unreadable and every one of them looked perfect.
 *
 * So the real verification was done once, out of band, against OpenCV — an
 * implementation sharing no code, no tables and no author with this one:
 *
 *   pip install opencv-python-headless numpy
 *   node --experimental-strip-types tools/qr_fixtures.mjs | python3 tools/qr_roundtrip.py
 *
 * That produced three independent results, recorded here because they are the
 * evidence behind the cheap assertions below:
 *
 *  1. 165 symbols round-tripped through OpenCV's *detector* — every case below
 *     the ceiling that detector was measured to have (73 modules; past that it
 *     cannot read symbols its own encoder produced).
 *  2. Pinned to the same mask, this encoder's output is byte-for-byte identical
 *     to OpenCV's *encoder* at versions 2, 7, 9, 12, 14, 33 and 40 across all
 *     four levels — which is what actually proves the ECC tables, the block
 *     interleave, the zigzag placement, the masks and both BCH fields.
 *  3. The two disagreements that remain are understood and benign: a single
 *     remainder-bit module at versions with a non-zero remainder (a don't-care
 *     region no decoder reads), and mask preference, where both choices are
 *     legal because the symbol declares which one it used.
 *
 * CI has no OpenCV and does not need it. What runs here are the invariants
 * that survive without a decoder — the layout count, the format-bit round trip
 * and golden digests of output verified by the process above.
 */

const ECC_LEVELS: EccLevel[] = ['L', 'M', 'Q', 'H'];

function digestOf(modules: readonly (readonly boolean[])[]): string {
	const flat = modules.map((row) => row.map((m) => (m ? '1' : '0')).join('')).join('');
	return createHash('sha256').update(flat).digest('hex').slice(0, 16);
}

/**
 * Read the format field back out of a finished symbol, undoing the BCH mask.
 *
 * Deliberately a second implementation of the placement rather than a call
 * into the encoder's: a test that reuses the code under test to check that
 * code agrees with itself.
 */
function readFormat(
	modules: readonly (readonly boolean[])[],
	copy: 1 | 2
): { ecc: number; mask: number } {
	const size = modules.length;
	const bits: boolean[] = [];
	if (copy === 1) {
		for (let i = 0; i <= 5; i++) bits.push(modules[i][8]);
		bits.push(modules[7][8], modules[8][8], modules[8][7]);
		for (let i = 9; i < 15; i++) bits.push(modules[8][14 - i]);
	} else {
		for (let i = 0; i < 8; i++) bits.push(modules[8][size - 1 - i]);
		for (let i = 8; i < 15; i++) bits.push(modules[size - 15 + i][8]);
	}
	let value = 0;
	bits.forEach((bit, i) => (value |= (bit ? 1 : 0) << i));
	value ^= 0x5412;
	const data = (value >>> 10) & 0x1f;
	return { ecc: (data >>> 3) & 0x03, mask: data & 0x07 };
}

describe('capacity', () => {
	it('agrees with the spec formula about how many modules carry data', () => {
		// The single number that says whether the function-pattern layout is
		// right. An alignment pattern one module off, or a version field
		// reserved on the wrong axis, changes this count and nothing else
		// observable — the symbol still has three finders and still looks fine.
		for (let version = MIN_VERSION; version <= MAX_VERSION; version++) {
			const reserved = functionModules(version);
			const free = reserved.reduce(
				(n, row) => n + row.reduce((m, taken) => m + (taken ? 0 : 1), 0),
				0
			);
			expect(free, `version ${version}`).toBe(rawDataModules(version));
		}
	});

	it('holds exactly the published byte-mode capacity, all 160 combinations', () => {
		// The two ECC tables in qr.ts are the only data that cannot be derived,
		// and a single wrong entry corrupts every symbol at that version and
		// level while leaving all 39 others perfect. Nothing structural catches
		// that — the layout count does not depend on the ECC tables at all.
		//
		// So the capacities are pinned. These 160 numbers were produced by
		// binary-searching OpenCV's encoder for the largest byte payload it
		// accepts at each forced version and level (see tools/qr_roundtrip.py's
		// header for the setup), and they match ISO/IEC 18004 table 7. They come
		// from an implementation that shares nothing with this one.
		const PUBLISHED: Record<EccLevel, readonly number[]> = {
			L: [17, 32, 53, 78, 106, 134, 154, 192, 230, 271, 321, 367, 425, 458, 520, 586, 644, 718, 792, 858, 929, 1003, 1091, 1171, 1273, 1367, 1465, 1528, 1628, 1732, 1840, 1952, 2068, 2188, 2303, 2431, 2563, 2699, 2809, 2953],
			M: [14, 26, 42, 62, 84, 106, 122, 152, 180, 213, 251, 287, 331, 362, 412, 450, 504, 560, 624, 666, 711, 779, 857, 911, 997, 1059, 1125, 1190, 1264, 1370, 1452, 1538, 1628, 1722, 1809, 1911, 1989, 2099, 2213, 2331],
			Q: [11, 20, 32, 46, 60, 74, 86, 108, 130, 151, 177, 203, 241, 258, 292, 322, 364, 394, 442, 482, 509, 565, 611, 661, 715, 751, 805, 868, 908, 982, 1030, 1112, 1168, 1228, 1283, 1351, 1423, 1499, 1579, 1663],
			H: [7, 14, 24, 34, 44, 58, 64, 84, 98, 119, 137, 155, 177, 194, 220, 250, 280, 310, 338, 382, 403, 439, 461, 511, 535, 593, 625, 658, 698, 742, 790, 842, 898, 958, 983, 1051, 1093, 1139, 1219, 1273]
		};

		for (const ecc of ECC_LEVELS) {
			for (let v = MIN_VERSION; v <= MAX_VERSION; v++) {
				// 4 bits of mode plus the character-count field, then whole bytes.
				const capacity = dataCodewords(v, ecc) - (v <= 9 ? 2 : 3);
				expect(capacity, `${ecc} v${v}`).toBe(PUBLISHED[ecc][v - 1]);
			}
		}
	});

	it('grows with version and shrinks with error correction', () => {
		for (const ecc of ECC_LEVELS) {
			for (let v = MIN_VERSION; v < MAX_VERSION; v++) {
				expect(dataCodewords(v + 1, ecc), `${ecc} v${v}->v${v + 1}`).toBeGreaterThan(
					dataCodewords(v, ecc)
				);
			}
		}
		for (let v = MIN_VERSION; v <= MAX_VERSION; v++) {
			expect(dataCodewords(v, 'L')).toBeGreaterThan(dataCodewords(v, 'M'));
			expect(dataCodewords(v, 'M')).toBeGreaterThan(dataCodewords(v, 'Q'));
			expect(dataCodewords(v, 'Q')).toBeGreaterThan(dataCodewords(v, 'H'));
		}
	});

	it('picks the smallest version that fits, and refuses what does not', () => {
		expect(encodeQr('a').version).toBe(1);

		// One byte over a version's capacity must step up exactly one version.
		for (const ecc of ECC_LEVELS) {
			for (const v of [1, 5, 9, 10, 20]) {
				const capacity = dataCodewords(v, ecc) - (v <= 9 ? 2 : 3);
				expect(encodeQr('x'.repeat(capacity), { ecc }).version, `${ecc} v${v} full`).toBe(v);
				expect(
					encodeQr('x'.repeat(capacity + 1), { ecc }).version,
					`${ecc} v${v} overfull`
				).toBeGreaterThan(v);
			}
		}

		expect(() => encodeQr('x'.repeat(4000))).toThrow(QrError);
	});

	it('counts bytes, not characters', () => {
		// Byte mode is bytes: a three-byte character costs three. Getting this
		// wrong overflows the version silently and produces a corrupt symbol
		// rather than an error.
		const ascii = encodeQr('aaa', { ecc: 'H' });
		const wide = encodeQr('✦✦✦', { ecc: 'H' }); // 3 bytes each in UTF-8
		expect(wide.version).toBeGreaterThan(ascii.version);
	});
});

describe('symbol structure', () => {
	const code = encodeQr('structure', { ecc: 'M' });

	it('places three finder patterns and no fourth', () => {
		const { modules, size } = code;
		const finderAt = (top: number, left: number) => {
			for (let dy = 0; dy < 7; dy++) {
				for (let dx = 0; dx < 7; dx++) {
					const ring = Math.max(Math.abs(dy - 3), Math.abs(dx - 3));
					if (modules[top + dy][left + dx] !== (ring !== 2)) return false;
				}
			}
			return true;
		};
		expect(finderAt(0, 0), 'top-left').toBe(true);
		expect(finderAt(0, size - 7), 'top-right').toBe(true);
		expect(finderAt(size - 7, 0), 'bottom-left').toBe(true);
		// The bottom-right corner must NOT be one; a scanner uses its absence
		// to work out the symbol's orientation.
		expect(finderAt(size - 7, size - 7), 'bottom-right').toBe(false);
	});

	it('alternates the timing patterns and keeps the always-dark module', () => {
		const { modules, size } = code;
		for (let i = 8; i < size - 8; i++) {
			expect(modules[6][i], `row 6 col ${i}`).toBe(i % 2 === 0);
			expect(modules[i][6], `col 6 row ${i}`).toBe(i % 2 === 0);
		}
		// Written by the function patterns and then very nearly overwritten by
		// the second format copy; the copy stops one row short of it on purpose.
		expect(modules[size - 8][8], 'always-dark module').toBe(true);
	});

	it('declares its own mask and level, identically in both copies', () => {
		const eccBits: Record<EccLevel, number> = { L: 1, M: 0, Q: 3, H: 2 };
		for (const ecc of ECC_LEVELS) {
			for (let mask = 0; mask < 8; mask++) {
				const symbol = encodeQr('format field', { ecc, forceMask: mask });
				expect(symbol.mask).toBe(mask);
				for (const copy of [1, 2] as const) {
					const read = readFormat(symbol.modules, copy);
					expect(read.mask, `${ecc}/mask${mask} copy ${copy}`).toBe(mask);
					expect(read.ecc, `${ecc}/mask${mask} copy ${copy}`).toBe(eccBits[ecc]);
				}
			}
		}
	});

	it('rejects a mask that is not 0-7', () => {
		expect(() => encodeQr('x', { forceMask: 8 })).toThrow(QrError);
		expect(() => functionModules(41)).toThrow(QrError);
	});

	it('chooses a mask by score rather than always taking the first', () => {
		// A masking bug that scored every candidate identically would silently
		// pin mask 0 forever and still produce readable codes — just worse ones.
		const masks = new Set(
			['a', 'bb', 'ccc', 'hello world', 'https://jarvis.local:8080/', 'x'.repeat(90)].map(
				(t) => encodeQr(t).mask
			)
		);
		expect(masks.size).toBeGreaterThan(1);
	});
});

describe('golden symbols', () => {
	// Digests of output that was verified byte-for-byte against OpenCV by the
	// process described at the top of this file. If one of these changes, the
	// encoder's output changed: re-run tools/qr_roundtrip.py and only then
	// update the digest. Do not "fix" a golden to make a test pass.
	const GOLDENS = [
		{ text: 'jarvis://pair?u=https%3A%2F%2Fjarvis.local%3A8080&c=7QK2-9F4M-XZ1T', ecc: 'M', version: 5, mask: 6, digest: 'fa9df9f30129dff7' },
		{ text: 'a', ecc: 'L', version: 1, mask: 0, digest: 'c8b94007197ad57d' },
		{ text: 'a', ecc: 'M', version: 1, mask: 5, digest: '2e7add7dfd3288d4' },
		{ text: 'a', ecc: 'Q', version: 1, mask: 0, digest: '9ef8181a0d43e2fe' },
		{ text: 'a', ecc: 'H', version: 1, mask: 0, digest: 'bb4ebea10d02c0ed' },
		{ text: 'https://jarvis.local:8080/', ecc: 'M', version: 2, mask: 4, digest: '8945fdb246d58cfb' },
		{ text: 'Jarvis — arc reactor ✦ ünïcøde', ecc: 'Q', version: 4, mask: 2, digest: '7af5859d15b1dae5' },
		{ text: 'x'.repeat(300), ecc: 'H', version: 18, mask: 3, digest: '9838cdce1ca2b614' }
	] as const;

	it.each(GOLDENS)('$ecc $version: $digest', ({ text, ecc, version, mask, digest }) => {
		const code = encodeQr(text, { ecc });
		expect(code.version).toBe(version);
		expect(code.mask).toBe(mask);
		expect(code.size).toBe(version * 4 + 17);
		expect(digestOf(code.modules)).toBe(digest);
	});
});

describe('svg', () => {
	it('sizes the viewBox for the symbol plus its quiet zone', () => {
		const svg = qrSvg('quiet', { ecc: 'M', quietZone: 4 });
		const code = encodeQr('quiet', { ecc: 'M' });
		expect(svg).toContain(`viewBox="0 0 ${code.size + 8} ${code.size + 8}"`);
		// The quiet zone is not decoration: without it a scanner cannot find the
		// finder patterns against whatever the page puts next to the code.
		expect(qrSvg('quiet', { quietZone: 0 })).toContain(`viewBox="0 0 ${code.size} ${code.size}"`);
	});

	it('paints an opaque background, because a transparent QR does not scan', () => {
		const svg = qrSvg('opaque');
		expect(svg).toMatch(/<rect width="\d+" height="\d+" fill="#ffffff"\/>/);
	});

	it('is one path, not one element per module', () => {
		const svg = qrSvg('x'.repeat(300), { ecc: 'H' });
		expect((svg.match(/<path/g) ?? []).length).toBe(1);
		expect(svg).not.toContain('<rect x=');
	});

	it('escapes what goes into attributes', () => {
		const svg = qrSvg('x', { title: 'a "quoted" <tag> & ampersand' });
		expect(svg).toContain('aria-label="a &quot;quoted&quot; &lt;tag&gt; &amp; ampersand"');
		expect(svg).not.toContain('<tag>');
	});

	it('produces well-formed XML', () => {
		// Cheap structural check; the page inlines this with {@html}, so a
		// malformed string is a broken panel rather than a decoding failure.
		const svg = qrSvg('jarvis://pair?u=x&c=y');
		expect(svg.startsWith('<svg ')).toBe(true);
		expect(svg.endsWith('</svg>')).toBe(true);
		expect((svg.match(/</g) ?? []).length).toBe((svg.match(/>/g) ?? []).length);
	});
});
