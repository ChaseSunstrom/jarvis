// The design tokens, as data.
//
// `src/lib/styles/tokens.css` declares the same names on `:root`; this module is
// what TypeScript reads when a value has to reach JavaScript (the HUD's per-state
// accent, the boot sequence's colours). `tokens.test.ts` diffs the two, so a
// value can never be changed in one place and not the other.

/** Every `--jv-*` custom property, in the order tokens.css declares them. */
export const TOKENS = {
	'--jv-bg': '#04070c',
	'--jv-bg-raised': '#06121a',
	'--jv-panel': 'rgba(6, 18, 26, 0.72)',
	'--jv-panel-solid': '#06121a',
	'--jv-field': 'rgba(4, 12, 18, 0.85)',
	'--jv-accent': '#3fd8ff',
	// Held, not failed: an approval waiting on a human, and an automation whose
	// run needs one. Distinct from the error red, which means something went
	// wrong rather than something is waiting.
	'--jv-warn': '#ffb347',
	'--jv-accent-deep': '#2bb0d8',
	'--jv-accent-ink': '#04121a',
	'--jv-amber': '#ff9e2c',
	'--jv-gold': '#ffcf5c',
	'--jv-danger': '#ff6b5c',
	'--jv-danger-text': '#ff9184',
	'--jv-ok': '#6ff2c0',
	'--jv-text': '#d7edf5',
	'--jv-text-bright': '#eaf7fc',
	'--jv-text-dim': '#9fc0cc',
	'--jv-text-faint': '#8fb3c0',
	'--jv-line': 'rgba(63, 216, 255, 0.32)',
	'--jv-line-soft': 'rgba(63, 216, 255, 0.12)',
	'--jv-line-hair': 'rgba(63, 216, 255, 0.08)',
	'--jv-wash': 'rgba(63, 216, 255, 0.08)',
	'--jv-wash-strong': 'rgba(63, 216, 255, 0.18)',
	'--jv-font-chrome':
		"'SFMono-Regular', ui-monospace, 'Cascadia Code', 'Cascadia Mono', Menlo, Consolas, monospace",
	'--jv-font-body': "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
	'--jv-fs-2xs': '0.55rem',
	'--jv-fs-xs': '0.62rem',
	'--jv-fs-sm': '0.68rem',
	'--jv-fs-md': '0.8rem',
	'--jv-fs-lg': '0.95rem',
	'--jv-fs-xl': 'clamp(0.95rem, 2vw, 1.15rem)',
	'--jv-fs-display': 'clamp(1.2rem, 3.2vw, 1.9rem)',
	'--jv-track-tight': '0.08em',
	'--jv-track-chrome': '0.16em',
	'--jv-track-wide': '0.24em',
	'--jv-track-logo': '0.5em',
	'--jv-space-1': '0.25rem',
	'--jv-space-2': '0.45rem',
	'--jv-space-3': '0.7rem',
	'--jv-space-4': '0.95rem',
	'--jv-space-5': '1.4rem',
	'--jv-radius-sm': '3px',
	'--jv-radius-md': '6px',
	'--jv-radius-pill': '999px',
	'--jv-glow-sm': '0 0 12px rgba(63, 216, 255, 0.16)',
	'--jv-glow-md': '0 0 18px rgba(63, 216, 255, 0.28)',
	'--jv-glow-lg': '0 0 34px rgba(63, 216, 255, 0.45)',
	'--jv-elev-panel': '0 18px 40px -28px rgba(0, 0, 0, 0.95)',
	'--jv-elev-float': '0 26px 60px -22px rgba(0, 0, 0, 0.95)',
	'--jv-grid-size': '46px',
	'--jv-grid-mask': 'radial-gradient(ellipse 85% 70% at 50% 30%, #000 30%, transparent 90%)',
	'--jv-bracket-size': 'clamp(20px, 3vw, 38px)',
	'--jv-bracket-inset': '10px',
	'--jv-dur-instant': '90ms',
	'--jv-dur-fast': '120ms',
	'--jv-dur-base': '180ms',
	'--jv-dur-slow': '320ms',
	'--jv-dur-pulse': '620ms',
	'--jv-ease-out': 'cubic-bezier(0.22, 0.61, 0.36, 1)',
	'--jv-ease-in-out': 'cubic-bezier(0.65, 0, 0.35, 1)',
	'--jv-ease-overshoot': 'cubic-bezier(0.34, 1.56, 0.64, 1)',
	'--jv-stagger-step': '26ms',
	'--jv-stagger-cap': '320ms',
	'--jv-drift': '6px',
	'--jv-focus-outline': '2px solid #3fd8ff',
	'--jv-focus-offset': '2px'
} as const;

export type TokenName = keyof typeof TOKENS;

/** Look a token up by name. Throws on a typo rather than emitting `undefined`. */
export function token(name: TokenName): string {
	const value = TOKENS[name];
	if (value === undefined) throw new Error(`unknown design token ${name}`);
	return value;
}

/** `var(--jv-…)`, for inline styles that want the live (overridable) value. */
export function cssVar(name: TokenName): string {
	return `var(${name})`;
}

/**
 * The HUD's accent per pipeline state. The orb, the grid, the brackets and the
 * glow all derive from this one colour, which is why it lives in JS: CSS cannot
 * pick it from `data-state` without repeating every rule five times.
 */
export const STATE_ACCENT = {
	idle: TOKENS['--jv-accent-deep'],
	listening: TOKENS['--jv-accent'],
	thinking: TOKENS['--jv-amber'],
	speaking: TOKENS['--jv-gold'],
	error: TOKENS['--jv-danger']
} as const;

export type AccentState = keyof typeof STATE_ACCENT;

export function accentFor(state: string, isError = false): string {
	if (isError) return STATE_ACCENT.error;
	return STATE_ACCENT[state as AccentState] ?? STATE_ACCENT.idle;
}
