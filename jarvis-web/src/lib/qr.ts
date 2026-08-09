/**
 * A QR encoder, in the console, with no dependencies.
 *
 * The console has to put a pairing code on screen for the phone to scan, and
 * it cannot fetch one: `svelte.config.js` sets `script-src: self` and
 * `img-src: self data:`, so a CDN library and a `chart.googleapis.com` QR
 * image are both blocked, and asking the *server* to render one would send the
 * pairing secret through another hop for no benefit. So: byte mode, versions
 * 1-40, all four error-correction levels, about 300 lines.
 *
 * Structure follows ISO/IEC 18004. The two tables below are the only data that
 * cannot be derived; everything else — codeword counts, block splits,
 * alignment positions — is computed, because a 160-row hand-copied table is a
 * transcription error waiting to happen and a computed one is not.
 *
 * Verified by round trip: `qr.test.ts` encodes at every version and level and
 * `tools/qr_roundtrip.py` decodes the result with OpenCV, which shares no code
 * with this file. A QR encoder that is subtly wrong still *looks* like a QR
 * code, so "it renders" proves nothing and an independent decoder proves
 * everything.
 */

export type EccLevel = 'L' | 'M' | 'Q' | 'H';

/** Error-correction codewords per block, indexed [level][version]. */
const ECC_CODEWORDS_PER_BLOCK: Record<EccLevel, readonly number[]> = {
	// version:  0(unused) 1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40
	L: [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
	M: [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
	Q: [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
	H: [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30]
};

/** Number of error-correction blocks, indexed [level][version]. */
const NUM_ECC_BLOCKS: Record<EccLevel, readonly number[]> = {
	L: [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
	M: [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
	Q: [-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
	H: [-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81]
};

/** Format-information bits are protected by BCH(15, 5) under this generator. */
const FORMAT_GENERATOR = 0x537;
/** Version information (v >= 7) is protected by BCH(18, 6). */
const VERSION_GENERATOR = 0x1f25;
/** The fixed XOR applied to format bits, so an all-zero format is not valid. */
const FORMAT_MASK = 0x5412;

const ECC_FORMAT_BITS: Record<EccLevel, number> = { L: 1, M: 0, Q: 3, H: 2 };

export const MIN_VERSION = 1;
export const MAX_VERSION = 40;

export interface QrCode {
	/** Side length in modules. */
	readonly size: number;
	/** `modules[y][x]`, true where the module is dark. */
	readonly modules: readonly (readonly boolean[])[];
	readonly version: number;
	readonly ecc: EccLevel;
	/** The mask pattern chosen by the penalty scoring, 0-7. */
	readonly mask: number;
}

export class QrError extends Error {}

// --- capacity ---------------------------------------------------------------

/** Centres of the alignment patterns for a version, in module coordinates. */
function alignmentPositions(version: number): number[] {
	if (version === 1) return [];
	const count = Math.floor(version / 7) + 2;
	const step =
		version === 32
			? 26
			: Math.ceil((version * 4 + 4) / (count * 2 - 2)) * 2;
	const positions = [6];
	for (let pos = version * 4 + 10; positions.length < count; pos -= step) positions.unshift(pos);
	return positions;
}

/**
 * Modules available for data and ECC, before the format/version fields.
 *
 * Exported because it is the one number that says whether the function-pattern
 * layout is right: lay out the finders, timing, alignment, format and version
 * fields, count what is left, and it must equal this. An alignment pattern one
 * module wide of where it belongs, or a version field reserved on the wrong
 * axis, changes that count and nothing else observable — the symbol still looks
 * like a QR code and still has three finders.
 */
export function rawDataModules(version: number): number {
	let result = (16 * version + 128) * version + 64;
	if (version >= 2) {
		const align = Math.floor(version / 7) + 2;
		result -= (25 * align - 10) * align - 55;
		if (version >= 7) result -= 36;
	}
	return result;
}

/** Total codewords (data + ECC) a version holds. */
function totalCodewords(version: number): number {
	return Math.floor(rawDataModules(version) / 8);
}

/** Data codewords a (version, level) holds, after ECC is deducted. */
export function dataCodewords(version: number, ecc: EccLevel): number {
	return (
		totalCodewords(version) - ECC_CODEWORDS_PER_BLOCK[ecc][version] * NUM_ECC_BLOCKS[ecc][version]
	);
}

/** Bits the byte-mode character count field takes at this version. */
function countBits(version: number): number {
	return version <= 9 ? 8 : 16;
}

// --- Reed-Solomon over GF(256) ---------------------------------------------

/**
 * Multiply in GF(2^8) modulo x^8 + x^4 + x^3 + x^2 + 1 (0x11d), the field the
 * QR spec uses. Russian-peasant, so there is no log table to get wrong.
 */
function gfMul(x: number, y: number): number {
	let z = 0;
	for (let i = 7; i >= 0; i--) {
		z = (z << 1) ^ ((z >>> 7) * 0x11d);
		z ^= ((y >>> i) & 1) * x;
	}
	return z & 0xff;
}

/** Coefficients of the divisor polynomial for `degree` ECC codewords. */
function rsDivisor(degree: number): Uint8Array {
	const result = new Uint8Array(degree);
	result[degree - 1] = 1;
	let root = 1;
	for (let i = 0; i < degree; i++) {
		for (let j = 0; j < degree; j++) {
			result[j] = gfMul(result[j], root);
			if (j + 1 < degree) result[j] ^= result[j + 1];
		}
		root = gfMul(root, 0x02);
	}
	return result;
}

function rsRemainder(data: Uint8Array, divisor: Uint8Array): Uint8Array {
	const result = new Uint8Array(divisor.length);
	for (const b of data) {
		const factor = b ^ result[0];
		result.copyWithin(0, 1);
		result[result.length - 1] = 0;
		for (let i = 0; i < result.length; i++) result[i] ^= gfMul(divisor[i], factor);
	}
	return result;
}

// --- bit buffer -------------------------------------------------------------

class BitBuffer {
	readonly bits: number[] = [];

	append(value: number, width: number): void {
		for (let i = width - 1; i >= 0; i--) this.bits.push((value >>> i) & 1);
	}
}

// --- encoding ---------------------------------------------------------------

/** UTF-8 bytes, because byte mode is bytes and the payload may not be ASCII. */
function utf8(text: string): Uint8Array {
	return new TextEncoder().encode(text);
}

function chooseVersion(byteLength: number, ecc: EccLevel, minVersion: number): number {
	for (let version = Math.max(MIN_VERSION, minVersion); version <= MAX_VERSION; version++) {
		const capacityBits = dataCodewords(version, ecc) * 8;
		const needed = 4 + countBits(version) + byteLength * 8;
		if (needed <= capacityBits) return version;
	}
	throw new QrError(
		`${byteLength} bytes will not fit in a version-${MAX_VERSION} code at level ${ecc}`
	);
}

/** Data codewords, padded to the version's capacity. */
function buildCodewords(data: Uint8Array, version: number, ecc: EccLevel): Uint8Array {
	const capacity = dataCodewords(version, ecc) * 8;
	const bb = new BitBuffer();
	bb.append(0b0100, 4); // byte mode
	bb.append(data.length, countBits(version));
	for (const b of data) bb.append(b, 8);

	// Terminator, then to a byte boundary, then the spec's alternating padding.
	bb.append(0, Math.min(4, capacity - bb.bits.length));
	bb.append(0, (8 - (bb.bits.length % 8)) % 8);
	for (let pad = 0xec; bb.bits.length < capacity; pad ^= 0xec ^ 0x11) bb.append(pad, 8);

	const out = new Uint8Array(bb.bits.length / 8);
	bb.bits.forEach((bit, i) => (out[i >>> 3] |= bit << (7 - (i & 7))));
	return out;
}

/** Split into blocks, add ECC, and interleave as the spec requires. */
function interleave(data: Uint8Array, version: number, ecc: EccLevel): Uint8Array {
	const numBlocks = NUM_ECC_BLOCKS[ecc][version];
	const eccLen = ECC_CODEWORDS_PER_BLOCK[ecc][version];
	const total = totalCodewords(version);
	const shortBlocks = numBlocks - (total % numBlocks);
	const shortLen = Math.floor(total / numBlocks) - eccLen;

	const divisor = rsDivisor(eccLen);
	const blocks: Uint8Array[] = [];
	const eccBlocks: Uint8Array[] = [];
	for (let i = 0, k = 0; i < numBlocks; i++) {
		const len = shortLen + (i < shortBlocks ? 0 : 1);
		const block = data.subarray(k, k + len);
		k += len;
		blocks.push(block);
		eccBlocks.push(rsRemainder(block, divisor));
	}

	const result = new Uint8Array(total);
	let at = 0;
	for (let i = 0; i < shortLen + 1; i++) {
		for (let b = 0; b < numBlocks; b++) {
			// Short blocks have no codeword in the last data column.
			if (i < blocks[b].length) result[at++] = blocks[b][i];
		}
	}
	for (let i = 0; i < eccLen; i++) {
		for (let b = 0; b < numBlocks; b++) result[at++] = eccBlocks[b][i];
	}
	return result;
}

// --- module placement -------------------------------------------------------

type Grid = boolean[][];

function blank(size: number): Grid {
	return Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
}

function drawFinder(modules: Grid, reserved: Grid, cx: number, cy: number): void {
	const size = modules.length;
	for (let dy = -4; dy <= 4; dy++) {
		for (let dx = -4; dx <= 4; dx++) {
			const x = cx + dx;
			const y = cy + dy;
			if (x < 0 || x >= size || y < 0 || y >= size) continue;
			const d = Math.max(Math.abs(dx), Math.abs(dy));
			modules[y][x] = d !== 2 && d !== 4;
			reserved[y][x] = true;
		}
	}
}

function drawFunctionPatterns(modules: Grid, reserved: Grid, version: number): void {
	const size = modules.length;

	// Timing patterns, before the finders so the finders' separators win.
	for (let i = 0; i < size; i++) {
		modules[6][i] = i % 2 === 0;
		modules[i][6] = i % 2 === 0;
		reserved[6][i] = true;
		reserved[i][6] = true;
	}

	drawFinder(modules, reserved, 3, 3);
	drawFinder(modules, reserved, size - 4, 3);
	drawFinder(modules, reserved, 3, size - 4);

	const align = alignmentPositions(version);
	for (const cy of align) {
		for (const cx of align) {
			// Not over a finder.
			const corner =
				(cx === 6 && cy === 6) ||
				(cx === 6 && cy === size - 7) ||
				(cx === size - 7 && cy === 6);
			if (corner) continue;
			for (let dy = -2; dy <= 2; dy++) {
				for (let dx = -2; dx <= 2; dx++) {
					modules[cy + dy][cx + dx] = Math.max(Math.abs(dx), Math.abs(dy)) !== 1;
					reserved[cy + dy][cx + dx] = true;
				}
			}
		}
	}

	// Format-information area, filled in later; reserved now so data skips it.
	for (let i = 0; i <= 8; i++) {
		if (i !== 6) {
			reserved[8][i] = true;
			reserved[i][8] = true;
		}
	}
	for (let i = 0; i < 8; i++) {
		reserved[8][size - 1 - i] = true;
		reserved[size - 1 - i][8] = true;
	}
	// The one module that is always dark.
	modules[size - 8][8] = true;
	reserved[size - 8][8] = true;

	if (version >= 7) {
		for (let i = 0; i < 18; i++) {
			const a = size - 11 + (i % 3);
			const b = Math.floor(i / 3);
			reserved[a][b] = true;
			reserved[b][a] = true;
		}
	}
}

function drawFormatBits(modules: Grid, ecc: EccLevel, mask: number): void {
	const size = modules.length;
	const data = (ECC_FORMAT_BITS[ecc] << 3) | mask;
	let rem = data;
	for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * FORMAT_GENERATOR);
	const bits = (((data << 10) | rem) ^ FORMAT_MASK) & 0x7fff;
	const bit = (i: number) => ((bits >>> i) & 1) === 1;

	// Copy one, around the top-left finder: bits 0-5 down column 8, then the
	// corner, then bits 9-14 back along row 8. Note the axes — the spec writes
	// these as (x, y) and this grid is [y][x], which is exactly the transposition
	// that made every symbol here undecodable while still looking like a QR code.
	for (let i = 0; i <= 5; i++) modules[i][8] = bit(i);
	modules[7][8] = bit(6);
	modules[8][8] = bit(7);
	modules[8][7] = bit(8);
	for (let i = 9; i < 15; i++) modules[8][14 - i] = bit(i);

	// Copy two, split between the other two finders: bits 0-7 along row 8 from
	// the right edge, bits 8-14 up column 8 from the bottom. The second run
	// stops at row size-7, one short of the always-dark module at size-8.
	for (let i = 0; i < 8; i++) modules[8][size - 1 - i] = bit(i);
	for (let i = 8; i < 15; i++) modules[size - 15 + i][8] = bit(i);
}

function drawVersionBits(modules: Grid, version: number): void {
	if (version < 7) return;
	const size = modules.length;
	let rem = version;
	for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * VERSION_GENERATOR);
	const bits = (version << 12) | rem;
	for (let i = 0; i < 18; i++) {
		const on = ((bits >>> i) & 1) === 1;
		const a = size - 11 + (i % 3);
		const b = Math.floor(i / 3);
		modules[a][b] = on;
		modules[b][a] = on;
	}
}

/** Zigzag from the bottom-right, two columns at a time, skipping column 6. */
function drawCodewords(modules: Grid, reserved: Grid, codewords: Uint8Array): void {
	const size = modules.length;
	let i = 0;
	for (let right = size - 1; right >= 1; right -= 2) {
		if (right === 6) right = 5;
		for (let vert = 0; vert < size; vert++) {
			for (let j = 0; j < 2; j++) {
				const x = right - j;
				const upward = ((right + 1) & 2) === 0;
				const y = upward ? size - 1 - vert : vert;
				if (reserved[y][x]) continue;
				if (i < codewords.length * 8) {
					modules[y][x] = ((codewords[i >>> 3] >>> (7 - (i & 7))) & 1) === 1;
					i++;
				}
				// Any modules past the data are left light, as the spec allows.
			}
		}
	}
}

function maskBit(mask: number, x: number, y: number): boolean {
	switch (mask) {
		case 0: return (x + y) % 2 === 0;
		case 1: return y % 2 === 0;
		case 2: return x % 3 === 0;
		case 3: return (x + y) % 3 === 0;
		case 4: return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0;
		case 5: return ((x * y) % 2) + ((x * y) % 3) === 0;
		case 6: return (((x * y) % 2) + ((x * y) % 3)) % 2 === 0;
		case 7: return (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
		default: throw new QrError(`mask ${mask} is not 0-7`);
	}
}

function applyMask(modules: Grid, reserved: Grid, mask: number): void {
	const size = modules.length;
	for (let y = 0; y < size; y++) {
		for (let x = 0; x < size; x++) {
			if (!reserved[y][x] && maskBit(mask, x, y)) modules[y][x] = !modules[y][x];
		}
	}
}

/** The spec's four penalty rules; the lowest-scoring mask is chosen. */
function penalty(modules: Grid): number {
	const size = modules.length;
	let score = 0;

	const runPenalty = (run: number) => (run >= 5 ? 3 + (run - 5) : 0);

	for (let y = 0; y < size; y++) {
		let run = 1;
		for (let x = 1; x < size; x++) {
			if (modules[y][x] === modules[y][x - 1]) run++;
			else {
				score += runPenalty(run);
				run = 1;
			}
		}
		score += runPenalty(run);
	}
	for (let x = 0; x < size; x++) {
		let run = 1;
		for (let y = 1; y < size; y++) {
			if (modules[y][x] === modules[y - 1][x]) run++;
			else {
				score += runPenalty(run);
				run = 1;
			}
		}
		score += runPenalty(run);
	}

	// 2x2 blocks of one colour.
	for (let y = 0; y < size - 1; y++) {
		for (let x = 0; x < size - 1; x++) {
			const c = modules[y][x];
			if (c === modules[y][x + 1] && c === modules[y + 1][x] && c === modules[y + 1][x + 1]) {
				score += 3;
			}
		}
	}

	// Finder-like 1:1:3:1:1 runs with four light modules on either side.
	const FINDER = [true, false, true, true, true, false, true];
	const looksLikeFinder = (get: (i: number) => boolean, at: number): boolean => {
		for (let i = 0; i < 7; i++) if (get(at + i) !== FINDER[i]) return false;
		const before = [at - 4, at - 3, at - 2, at - 1].every((i) => i < 0 || !get(i));
		const after = [at + 7, at + 8, at + 9, at + 10].every((i) => i >= size || !get(i));
		return before || after;
	};
	for (let y = 0; y < size; y++) {
		for (let x = 0; x <= size - 7; x++) {
			if (looksLikeFinder((i) => modules[y][i], x)) score += 40;
		}
	}
	for (let x = 0; x < size; x++) {
		for (let y = 0; y <= size - 7; y++) {
			if (looksLikeFinder((i) => modules[i][x], y)) score += 40;
		}
	}

	// Deviation from a 50/50 light/dark balance.
	let dark = 0;
	for (const row of modules) for (const m of row) if (m) dark++;
	const percent = (dark * 100) / (size * size);
	score += Math.floor(Math.abs(percent - 50) / 5) * 10;

	return score;
}

// --- the public surface -----------------------------------------------------

/**
 * The function-pattern layout for a version: true where a module is spoken for
 * by a finder, timing, alignment, format or version field and therefore carries
 * no data. Exported for the layout test described on [rawDataModules].
 */
export function functionModules(version: number): boolean[][] {
	if (version < MIN_VERSION || version > MAX_VERSION) {
		throw new QrError(`version ${version} is not ${MIN_VERSION}-${MAX_VERSION}`);
	}
	const size = version * 4 + 17;
	const modules = blank(size);
	const reserved = blank(size);
	drawFunctionPatterns(modules, reserved, version);
	return reserved;
}

export interface QrOptions {
	/** Error-correction level. M is the usual choice for a screen. */
	ecc?: EccLevel;
	/** Force at least this version, e.g. to keep a code's size stable. */
	minVersion?: number;
	/**
	 * Use this mask (0-7) instead of the lowest-penalty one.
	 *
	 * For tests only, and specifically for comparing this encoder against
	 * another one: two conformant encoders may legitimately choose different
	 * masks, which makes a byte-for-byte comparison of their output impossible
	 * unless the mask can be pinned. Every mask is legal — the symbol declares
	 * which one it used in its format bits — so this cannot produce an invalid
	 * code, only a worse-scoring one.
	 */
	forceMask?: number;
}

/** Encode `text` as a QR symbol. */
export function encodeQr(text: string, options: QrOptions = {}): QrCode {
	const ecc = options.ecc ?? 'M';
	const data = utf8(text);
	const version = chooseVersion(data.length, ecc, options.minVersion ?? MIN_VERSION);
	const codewords = interleave(buildCodewords(data, version, ecc), version, ecc);

	const size = version * 4 + 17;
	const base = blank(size);
	const reserved = blank(size);
	drawFunctionPatterns(base, reserved, version);
	drawVersionBits(base, version);
	drawCodewords(base, reserved, codewords);

	const masks =
		options.forceMask === undefined ? [0, 1, 2, 3, 4, 5, 6, 7] : [options.forceMask];
	let best: { modules: Grid; mask: number; score: number } | null = null;
	for (const mask of masks) {
		const modules = base.map((row) => row.slice());
		applyMask(modules, reserved, mask);
		drawFormatBits(modules, ecc, mask);
		const score = penalty(modules);
		if (!best || score < best.score) best = { modules, mask, score };
	}
	if (!best) throw new QrError('no mask was scored');

	return { size, modules: best.modules, version, ecc, mask: best.mask };
}

export interface QrSvgOptions extends QrOptions {
	/** Light modules around the symbol. The spec asks for 4; scanners need it. */
	quietZone?: number;
	/** Dark module colour. */
	dark?: string;
	/** Light module colour. Must be opaque: a transparent QR does not scan. */
	light?: string;
	/** Accessible name for the image. */
	title?: string;
}

/**
 * Render as a self-contained SVG string.
 *
 * One `<path>` of `M x y h1 v1 h-1 z` subpaths rather than a rect per module:
 * a version-10 code is 3249 modules and a rect each is a DOM node each.
 */
export function qrSvg(text: string, options: QrSvgOptions = {}): string {
	const { size, modules } = encodeQr(text, options);
	const quiet = options.quietZone ?? 4;
	const dark = options.dark ?? '#04070c';
	const light = options.light ?? '#ffffff';
	const side = size + quiet * 2;

	const parts: string[] = [];
	for (let y = 0; y < size; y++) {
		for (let x = 0; x < size; x++) {
			if (modules[y][x]) parts.push(`M${x + quiet} ${y + quiet}h1v1h-1z`);
		}
	}

	const title = options.title ?? 'QR code';
	return (
		`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${side} ${side}" ` +
		`shape-rendering="crispEdges" role="img" aria-label="${escapeAttr(title)}">` +
		`<rect width="${side}" height="${side}" fill="${escapeAttr(light)}"/>` +
		`<path d="${parts.join('')}" fill="${escapeAttr(dark)}"/>` +
		`</svg>`
	);
}

function escapeAttr(value: string): string {
	return value
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');
}
