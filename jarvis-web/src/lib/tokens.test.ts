// Four things this file will not let slide.
//
//  1. tokens.ts and tokens.css drifting apart. They are the same table written
//     twice (one for the cascade, one for JavaScript), and "I only changed the
//     CSS" is exactly how a HUD accent ends up not matching its own glow.
//  2. Text that cannot be read. The palette is bright-on-near-black, which is
//     flattering right up until a 0.55-opacity caption drops under 4.5:1.
//  3. Text that is too small to read, which is the same failure by another
//     route and is what the scale used to be.
//  4. A colour written anywhere but here. tokens.css has always SAID that
//     nothing else may write a raw hex; nothing checked, and by the time
//     anybody looked there were eleven of them.
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { STATE_ACCENT, TOKENS, accentFor, cssVar, token, tokenMs } from './tokens';

const cssPath = fileURLToPath(new URL('./styles/tokens.css', import.meta.url));
const css = readFileSync(cssPath, 'utf8');
/** The source of truth every generated file is checked against. */
const source = JSON.parse(
	readFileSync(fileURLToPath(new URL('../../../design/tokens.json', import.meta.url)), 'utf8')
);

/** The `--name: value` pairs declared on `:root`, in file order. */
function parseRootTokens(source: string): Record<string, string> {
	const block = source.slice(source.indexOf(':root'));
	const body = block.slice(block.indexOf('{') + 1, block.indexOf('}'));
	const out: Record<string, string> = {};
	for (const line of body.split('\n')) {
		const trimmed = line.trim();
		if (!trimmed.startsWith('--')) continue;
		const colon = trimmed.indexOf(':');
		out[trimmed.slice(0, colon).trim()] = trimmed.slice(colon + 1).replace(/;$/, '').trim();
	}
	return out;
}

const fromCss = parseRootTokens(css);

// --- WCAG contrast ---------------------------------------------------------

function channel(v: number): number {
	const s = v / 255;
	return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

function luminance(hex: string): number {
	const h = hex.replace('#', '');
	const n = parseInt(h.length === 3 ? h.replace(/(.)/g, '$1$1') : h, 16);
	return (
		0.2126 * channel((n >> 16) & 255) +
		0.7152 * channel((n >> 8) & 255) +
		0.0722 * (n & 255)
	);
}

/** Contrast ratio of `fg` over `bg`, optionally with `fg` at partial opacity. */
export function contrast(fg: string, bg: string, alpha = 1): number {
	const lf = luminance(fg) * alpha + luminance(bg) * (1 - alpha);
	const lb = luminance(bg);
	const [hi, lo] = lf > lb ? [lf, lb] : [lb, lf];
	return (hi + 0.05) / (lo + 0.05);
}

describe('design tokens', () => {
	it('exports every custom property tokens.css declares, with the same value', () => {
		expect(Object.keys(TOKENS).sort()).toEqual(Object.keys(fromCss).sort());
		for (const [name, value] of Object.entries(TOKENS)) {
			expect(fromCss[name], name).toBe(value);
		}
	});

	it('is generated from design/tokens.json, the one place a value is typed', () => {
		for (const name of ['bg', 'accent', 'amber', 'gold', 'danger', 'text', 'line'] as const) {
			expect(TOKENS[`--jv-${name}`], name).toBe(source.color[name].$value);
		}
		expect(TOKENS['--jv-wash']).toMatch(/^rgba\(/);
		expect(css.includes('@generated from design/tokens.json')).toBe(true);
	});

	it('reads a duration token as milliseconds', () => {
		expect(tokenMs('--jv-dur-base')).toBe(260);
		expect(tokenMs('--jv-rx-level')).toBe(3400);
		// A colour is a valid token name and still not a duration: the check is at runtime.
		expect(() => tokenMs('--jv-accent')).toThrow(/not a duration/);
	});

	it('names every token in the --jv- namespace', () => {
		for (const name of Object.keys(TOKENS)) expect(name.startsWith('--jv-')).toBe(true);
	});

	it('resolves tokens by name and refuses a typo', () => {
		expect(token('--jv-accent')).toBe(TOKENS['--jv-accent']);
		expect(cssVar('--jv-accent')).toBe('var(--jv-accent)');
		// @ts-expect-error — the point is that the name is not a valid token
		expect(() => token('--jv-nope')).toThrow(/unknown design token/);
	});

	it('maps every pipeline state to an accent, and anything else to idle', () => {
		expect(accentFor('listening')).toBe(STATE_ACCENT.listening);
		expect(accentFor('thinking')).toBe(TOKENS['--jv-amber']);
		expect(accentFor('speaking')).toBe(TOKENS['--jv-gold']);
		expect(accentFor('nonsense')).toBe(STATE_ACCENT.idle);
		// An error overrides whatever state the pipeline thinks it is in.
		expect(accentFor('speaking', true)).toBe(TOKENS['--jv-danger']);
	});
});

describe('the type scale', () => {
	/** The smallest rem in a token's value — a clamp's floor is what it lands on. */
	function smallestRem(value: string): number {
		const found = [...value.matchAll(/([\d.]+)rem/g)].map((m) => Number(m[1]));
		return found.length ? Math.min(...found) : NaN;
	}

	it('never asks anybody to read under eleven pixels', () => {
		// `--jv-fs-2xs` was 0.55rem — 8.8px at a default root — and it is the size
		// of every pill, every entity id and every caption in the console. This is
		// the floor, in rem, at a 16px root: 0.7rem is 11.2px.
		for (const [name, value] of Object.entries(TOKENS)) {
			if (!name.startsWith('--jv-fs-')) continue;
			const rem = smallestRem(value);
			expect(rem, `${name} = ${value}`).not.toBeNaN();
			expect(rem, `${name} = ${value}`).toBeGreaterThanOrEqual(0.7);
		}
	});

	it('keeps body text at least as large as the chrome around it', () => {
		const order = ['--jv-fs-2xs', '--jv-fs-xs', '--jv-fs-sm', '--jv-fs-md', '--jv-fs-lg'] as const;
		const sizes = order.map((name) => smallestRem(TOKENS[name]));
		expect([...sizes].sort((a, b) => a - b)).toEqual(sizes);
		// The step somebody reads a sentence in, rather than a label.
		expect(smallestRem(TOKENS['--jv-fs-md'])).toBeGreaterThanOrEqual(0.95);
	});
});

describe('where colours may be written', () => {
	/*
	 * The rule tokens.css states, enforced.
	 *
	 * Scope: stylesheets, and the `<style>` block of every component — which is
	 * where the design system actually lives. Script bodies are deliberately not
	 * scanned: `Orb.svelte` carries a GLSL shader that cannot read a custom
	 * property and says so at length, and `qr.ts` emits SVG fills for a code that
	 * has to survive being photographed. Both are documented exceptions to a rule
	 * about CSS, not quiet ones about colour.
	 *
	 * Black and white pass. Neither is a palette colour: the two in the tree are
	 * a mask stencil (only its alpha is read) and the paper a QR is printed on.
	 * Everything else — including a fallback inside `var(--jv-ok, #35d08a)`, which
	 * is how a token that did not exist went unnoticed for as long as it did —
	 * has to be a token.
	 */
	const srcDir = fileURLToPath(new URL('..', import.meta.url));
	const NEUTRAL = /^#(000|fff|000000|ffffff)$/i;

	function stylesheets(): { file: string; css: string }[] {
		const out: { file: string; css: string }[] = [];
		for (const entry of readdirSync(srcDir, { recursive: true, withFileTypes: true })) {
			if (!entry.isFile()) continue;
			const path = `${entry.parentPath}/${entry.name}`;
			const rel = path.slice(srcDir.length);
			if (rel.endsWith('styles/tokens.css')) continue;
			const text = readFileSync(path, 'utf8');
			if (entry.name.endsWith('.css')) {
				out.push({ file: rel, css: text });
			} else if (entry.name.endsWith('.svelte')) {
				for (const block of text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)) {
					out.push({ file: rel, css: block[1] });
				}
			}
		}
		return out;
	}

	it('finds the stylesheets it is supposed to be checking', () => {
		// A walk that silently matches nothing is a test that passes forever.
		const files = new Set(stylesheets().map((s) => s.file));
		expect(files.size).toBeGreaterThan(4);
		expect([...files].some((f) => f.endsWith('styles/chrome.css'))).toBe(true);
		expect([...files].some((f) => f.endsWith('.svelte'))).toBe(true);
	});

	it('has no raw hex outside tokens.css and tokens.ts', () => {
		const offences: string[] = [];
		for (const { file, css } of stylesheets()) {
			for (const line of css.split('\n')) {
				for (const hex of line.matchAll(/#[0-9a-fA-F]{3,8}\b/g)) {
					if (NEUTRAL.test(hex[0])) continue;
					offences.push(`${file}: ${line.trim()}`);
				}
			}
		}
		expect(offences, offences.join('\n')).toEqual([]);
	});
});

describe('colour contrast', () => {
	const bg = TOKENS['--jv-bg'];

	it('clears AA for body and chrome text on the page ground', () => {
		for (const name of [
			'--jv-text',
			'--jv-text-bright',
			'--jv-text-dim',
			'--jv-text-faint',
			'--jv-accent',
			'--jv-gold',
			'--jv-danger-text'
		] as const) {
			expect(contrast(TOKENS[name], bg), name).toBeGreaterThanOrEqual(4.5);
		}
	});

	it('still clears AA for the dimmest text at the lowest opacity the CSS uses', () => {
		// chrome.css never drops text below 0.75; check with margin at 0.7.
		expect(contrast(TOKENS['--jv-text-dim'], bg, 0.7)).toBeGreaterThanOrEqual(4.5);
	});

	it('gives the accent enough contrast to be a large-text/UI colour on the panel', () => {
		expect(contrast(TOKENS['--jv-accent'], TOKENS['--jv-panel-solid'])).toBeGreaterThanOrEqual(4.5);
	});

	it('keeps the "on" button readable: dark ink on the accent fill', () => {
		expect(contrast(TOKENS['--jv-accent-ink'], TOKENS['--jv-accent'])).toBeGreaterThanOrEqual(4.5);
	});
});
