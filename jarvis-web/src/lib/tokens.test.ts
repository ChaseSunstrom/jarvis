// Two things this file will not let slide.
//
//  1. tokens.ts and tokens.css drifting apart. They are the same table written
//     twice (one for the cascade, one for JavaScript), and "I only changed the
//     CSS" is exactly how a HUD accent ends up not matching its own glow.
//  2. Text that cannot be read. The palette is bright-on-near-black, which is
//     flattering right up until a 0.55-opacity caption drops under 4.5:1.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { STATE_ACCENT, TOKENS, accentFor, cssVar, token } from './tokens';

const cssPath = fileURLToPath(new URL('./styles/tokens.css', import.meta.url));
const css = readFileSync(cssPath, 'utf8');

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

	it('declares the palette the brief specifies', () => {
		expect(TOKENS['--jv-bg']).toBe('#04070c');
		expect(TOKENS['--jv-accent']).toBe('#3fd8ff');
		expect(TOKENS['--jv-amber']).toBe('#ff9e2c');
		expect(TOKENS['--jv-gold']).toBe('#ffcf5c');
		expect(TOKENS['--jv-danger']).toBe('#ff6b5c');
		expect(TOKENS['--jv-panel']).toMatch(/^rgba\(/);
	});

	it('names every token in the --jv- namespace', () => {
		for (const name of Object.keys(TOKENS)) expect(name.startsWith('--jv-')).toBe(true);
	});

	it('resolves tokens by name and refuses a typo', () => {
		expect(token('--jv-accent')).toBe('#3fd8ff');
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
