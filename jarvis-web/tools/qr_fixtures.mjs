// Emit one newline-delimited JSON case per (version, ecc) for qr_roundtrip.py.
//
// Each case is filled to within a few bytes of the version's stated capacity,
// because that is where an interleaving or padding bug shows up: a short
// payload at version 40 leaves the block structure barely exercised.
//
//   node --experimental-strip-types tools/qr_fixtures.mjs | python3 tools/qr_roundtrip.py
//
// Takes an optional version range: `... tools/qr_fixtures.mjs 1 10`.

import { encodeQr, dataCodewords, MAX_VERSION, MIN_VERSION } from '../src/lib/qr.ts';

const [fromArg, toArg] = process.argv.slice(2);
const from = Number(fromArg ?? MIN_VERSION);
const to = Number(toArg ?? MAX_VERSION);

// Deterministic filler with no repeating structure, so a mask that happens to
// suit a run of 'A's cannot flatter the result. No RNG: the fixtures must be
// the same on every run.
const ALPHABET = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~:/?#[]@!$&()*+,;=%';
function filler(n) {
	let out = '';
	for (let i = 0; i < n; i++) out += ALPHABET[(i * 31 + ((i * i) % 17)) % ALPHABET.length];
	return out;
}

for (let version = from; version <= to; version++) {
	for (const ecc of ['L', 'M', 'Q', 'H']) {
		// 4 bits of mode + 8/16 of count, then whole bytes.
		const capacity = dataCodewords(version, ecc) - (version <= 9 ? 2 : 3);
		for (const length of new Set([1, Math.floor(capacity / 2), capacity].filter((n) => n >= 1))) {
			const text = filler(length);
			const code = encodeQr(text, { ecc, minVersion: version });
			if (code.version !== version) {
				// The payload spilled into a larger version; nothing to assert
				// about `version` then, so skip rather than mislabel.
				continue;
			}
			process.stdout.write(
				JSON.stringify({
					label: `v${version}/${ecc}/${length}b/mask${code.mask}`,
					text,
					size: code.size,
					modules: code.modules.map((row) => row.map((m) => (m ? 1 : 0)))
				}) + '\n'
			);
		}
	}
}
