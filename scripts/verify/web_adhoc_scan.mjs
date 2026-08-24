// scripts/verify/web_adhoc_scan.mjs — find ad-hoc design values in a web tree.
//
// The target state says "no ad-hoc colors or spacing anywhere". This is the
// machine's reading of that sentence, applied to every .svelte/.css/.ts file
// under the given roots (default jarvis-web/src), except test files and files
// carrying the generated-token marker:
//
//   colour   any #hex literal; any rgb()/rgba()/hsl()/hsla() that is not the
//            relative-colour form `rgb(from var(--jv-…) …)`; color-mix() is
//            allowed because it composes tokens.
//   spacing  a numeric px/rem/em value in a spacing, sizing, type, radius,
//            shadow or motion property. `0` and `1px` (a hairline) are the
//            only literals allowed; everything else is a token or clamp() of
//            tokens.
//   motion   a numeric s/ms in transition/animation properties.
//
// Exit 1 with file:line for every hit. HTML entities (&#123;) are not colours.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

const roots = process.argv.slice(2).length ? process.argv.slice(2) : ['jarvis-web/src'];
const MARK = '@generated from design/tokens.json';
// The same documented exceptions the token lint honours, from the same file, so
// the two scanners cannot disagree about what the rule is: a GLSL shader cannot
// read a custom property, and a QR's two colours are what a camera needs.
const EXEMPT = new Set(
	Object.keys(
		JSON.parse(readFileSync(new URL('../../design/token-lint.baseline.json', import.meta.url), 'utf8'))
			.exceptions ?? {}
	)
);
const EXT = new Set(['.svelte', '.css', '.ts']);
const PROPS = /^\s*(?:margin|padding|gap|row-gap|column-gap|inset|top|right|bottom|left|width|height|min-width|max-width|min-height|max-height|font-size|letter-spacing|line-height|border-radius|box-shadow|text-shadow|transition|transition-duration|transition-delay|animation|animation-duration|animation-delay|translate|outline-offset)\s*:\s*([^;{}]*)/;

function walk(dir, out) {
	for (const name of readdirSync(dir)) {
		const p = join(dir, name);
		const st = statSync(p);
		if (st.isDirectory()) {
			if (name === 'node_modules' || name === 'build' || name === '.svelte-kit') continue;
			walk(p, out);
		} else if (EXT.has(extname(name)) && !/\.test\.ts$/.test(name) && !/\.d\.ts$/.test(name)) {
			out.push(p);
		}
	}
	return out;
}

const files = roots.flatMap((r) => {
	try { return walk(r, []); } catch { return []; }
});
const hits = [];
for (const file of files) {
	if (EXEMPT.has(file)) continue;
	const text = readFileSync(file, 'utf8');
	if (text.includes(MARK)) continue;
	const lines = text.split('\n');
	lines.forEach((line, i) => {
		const n = i + 1;
		const stripped = line.replace(/&#\d+;/g, '').replace(/\/\/.*$/, '');
		if (/#[0-9a-fA-F]{3,8}\b/.test(stripped) && !/^\s*\/\*|^\s*\*/.test(stripped)) {
			hits.push(`${file}:${n}: colour literal: ${line.trim()}`);
		}
		const fn = stripped.match(/\b(rgba?|hsla?)\(\s*([^)]*)/);
		if (fn && !/^from\b/.test(fn[2].trim())) hits.push(`${file}:${n}: colour function: ${line.trim()}`);
		const prop = stripped.match(PROPS);
		if (prop) {
			const value = prop[1].replace(/var\([^)]*\)/g, '').replace(/\b(0|1px)\b/g, '');
			if (/(?<![\w.-])\d*\.?\d+(px|rem|em|ms|s)\b/.test(value)) {
				hits.push(`${file}:${n}: raw value: ${line.trim()}`);
			}
		}
	});
}
if (hits.length) {
	for (const h of hits.slice(0, 200)) console.log(h);
	if (hits.length > 200) console.log(`… and ${hits.length - 200} more`);
	console.log(`\n${hits.length} ad-hoc value(s) in ${files.length} files`);
	process.exit(1);
}
console.log(`no ad-hoc design values in ${files.length} files`);
