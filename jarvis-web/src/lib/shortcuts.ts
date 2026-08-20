// Keyboard shortcuts for the management console.
//
// The console is a keyboard-first surface: `/` focuses the filter, `g d` goes
// to devices, Ctrl/Cmd-K opens the palette, Esc backs out. The tricky parts —
// "am I inside a text field", "is this the second key of a chord, or a fresh
// first key" — are pure functions here rather than conditionals scattered
// through a keydown handler.

/** A minimal keyboard event, so this module needs no DOM types. */
export interface KeyLike {
	key: string;
	ctrlKey?: boolean;
	metaKey?: boolean;
	altKey?: boolean;
	shiftKey?: boolean;
	repeat?: boolean;
}

/** An element-ish thing we may or may not want to steal keystrokes from. */
export interface TargetLike {
	tagName?: string;
	isContentEditable?: boolean;
	getAttribute?: (name: string) => string | null;
}

const TYPING_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

/**
 * True when the user is typing into something and a bare letter must be left
 * alone. Without this, `g` in a filter box navigates away mid-word.
 */
export function isTypingTarget(target: TargetLike | null | undefined): boolean {
	if (!target) return false;
	if (target.isContentEditable === true) return true;
	const tag = String(target.tagName ?? '').toUpperCase();
	if (TYPING_TAGS.has(tag)) return true;
	const role = target.getAttribute?.('role');
	return role === 'textbox' || role === 'combobox' || role === 'searchbox';
}

/** Ctrl-K / Cmd-K, the palette. */
export function isPaletteShortcut(e: KeyLike): boolean {
	return (e.ctrlKey === true || e.metaKey === true) && !e.altKey && e.key.toLowerCase() === 'k';
}

/** A bare key with no modifiers — the only kind a chord or `/` may consume. */
export function isBareKey(e: KeyLike): boolean {
	return !e.ctrlKey && !e.metaKey && !e.altKey;
}

/**
 * `g`-prefixed navigation chords. `g a` is automations; areas takes `g r`
 * (rooms) and tasks takes `g k` — `g t` was already tools. Code takes `g c`, n8n `g n`,
 * and skills `g l` (ski-LL-s), because `g s` was already settings.
 */
export const CHORDS: Readonly<Record<string, string>> = {
	'g d': '/devices',
	'g a': '/automations',
	'g r': '/areas',
	'g t': '/tools',
	'g k': '/tasks',
	'g c': '/code',
	'g n': '/n8n',
	'g l': '/skills',
	'g s': '/settings',
	'g h': '/'
};

/** How long a chord's first key stays armed. */
export const CHORD_TIMEOUT_MS = 1200;

export interface ChordResult {
	/** The route to go to, when the chord completed. */
	href?: string;
	/** True while a prefix is armed, so the UI can show a hint. */
	pending: boolean;
	/** The keys held so far, e.g. `'g'`. */
	prefix: string;
}

/**
 * Tracks multi-key chords. Stateless between chords: a first key that leads
 * nowhere, or one that arrives after the timeout, simply starts over.
 */
export class ChordTracker {
	private prefix = '';
	private armedAt = 0;

	constructor(
		private chords: Readonly<Record<string, string>> = CHORDS,
		private timeoutMs = CHORD_TIMEOUT_MS
	) {}

	/** The prefixes that can start a chord, derived from the table. */
	private get starters(): Set<string> {
		return new Set(Object.keys(this.chords).map((k) => k.split(' ')[0]));
	}

	reset(): void {
		this.prefix = '';
		this.armedAt = 0;
	}

	get pendingPrefix(): string {
		return this.prefix;
	}

	/**
	 * Feed one bare keypress. Returns the route when a chord completed.
	 * `now` is injected so the timeout is testable without waiting.
	 */
	press(key: string, now: number = Date.now()): ChordResult {
		const k = String(key ?? '').toLowerCase();
		if (k.length !== 1) {
			this.reset();
			return { pending: false, prefix: '' };
		}

		if (this.prefix && now - this.armedAt <= this.timeoutMs) {
			const href = this.chords[`${this.prefix} ${k}`];
			this.reset();
			if (href) return { href, pending: false, prefix: '' };
			// Not a chord after all — but this key may itself start one.
			return this.arm(k, now);
		}

		return this.arm(k, now);
	}

	private arm(k: string, now: number): ChordResult {
		if (this.starters.has(k)) {
			this.prefix = k;
			this.armedAt = now;
			return { pending: true, prefix: k };
		}
		this.reset();
		return { pending: false, prefix: '' };
	}
}
