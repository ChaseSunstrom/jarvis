// The power-on sequence, as pure functions of elapsed milliseconds.
//
// This is the web mirror of the Android app's `ui/BootTimeline.kt`: same six
// stages, same element vocabulary, same "one variable, no scheduled callbacks"
// discipline. `BootSequence.svelte` owns a single rAF loop and asks this module
// what to draw at time `t`; nothing here keeps state, so nothing can fall out of
// sync and every frame is reproducible in a unit test.
//
// Shortened to ~1.2 s for the web — a page load already costs the user a wait
// that a cold app start does not.
//
//     0 ms  black; one hairline scan line sweeps top -> bottom
//   110 ms  the reactor core ignites from a point, with a bloom flare
//   260 ms  rings materialise outward, one at a time, each overshooting
//   520 ms  "J A R V I S" resolves in, letter by letter, spacing settling
//   740 ms  three system-check lines type on in monospace
//  1030 ms  the chrome dissolves and the app fades up underneath
//
// The six stages tile [0, TOTAL_MS] exactly — no gap, no overlap — so "which
// stage is this" always has one answer. Individual elements do overlap (the
// scan line is still fading while the core ignites); that overlap lives in the
// per-element functions, not in the stage partition.

export const TOTAL_MS = 1200;

export const SCAN_START_MS = 0;
export const IGNITE_START_MS = 110;
export const RINGS_START_MS = 260;
export const WORDMARK_START_MS = 520;
export const CHECKS_START_MS = 740;
export const HANDOFF_START_MS = 1030;

export type BootStage = 'scan' | 'ignite' | 'rings' | 'wordmark' | 'checks' | 'handoff';

export interface StageWindow {
	stage: BootStage;
	startMs: number;
	endMs: number;
}

/** The six stages, in order, tiling the whole timeline. */
export const STAGES: readonly StageWindow[] = [
	{ stage: 'scan', startMs: SCAN_START_MS, endMs: IGNITE_START_MS },
	{ stage: 'ignite', startMs: IGNITE_START_MS, endMs: RINGS_START_MS },
	{ stage: 'rings', startMs: RINGS_START_MS, endMs: WORDMARK_START_MS },
	{ stage: 'wordmark', startMs: WORDMARK_START_MS, endMs: CHECKS_START_MS },
	{ stage: 'checks', startMs: CHECKS_START_MS, endMs: HANDOFF_START_MS },
	{ stage: 'handoff', startMs: HANDOFF_START_MS, endMs: TOTAL_MS }
];

// --- element timing ---------------------------------------------------------

/** The scan line finishes its sweep as the core ignites, then fades out. */
export const SCAN_FADE_MS = 90;

/** Core scale-in, from a point to full size. */
export const CORE_RISE_MS = 170;

/** The core's own fade-up: quicker than its scale, so it reads as ignition. */
export const CORE_FADE_MS = 85;

/** Bloom flare: up and back down, peaking halfway. */
export const FLARE_MS = 190;

/** Inner rim, dashed mid ring, fine dashes, gauge ticks. */
export const RING_COUNT = 4;
export const RING_STAGGER_MS = 55;
export const RING_MS = 110;

/** How far past its resting size a ring swings. */
export const RING_TENSION = 2.2;

/** J A R V I S. */
export const WORDMARK = 'JARVIS';
export const LETTER_COUNT = WORDMARK.length;
export const LETTER_STAGGER_MS = 24;
export const LETTER_MS = 110;

/** Letter spacing (em) at the start of the resolve, and at rest. */
export const LETTER_SPACING_START = 0.9;
export const LETTER_SPACING_END = 0.55;

/** Blur radius, in px, a letter starts with before it sharpens. */
export const LETTER_BLUR_PX = 7;

export const CHECK_STAGGER_MS = 80;

/** Time for one check line to type on, whatever its length. */
export const CHECK_TYPE_MS = 150;

/** Chrome fade at the handoff. */
export const HANDOFF_FADE_MS = 140;

/** The app starts fading up slightly after the chrome starts leaving. */
export const APP_FADE_DELAY_MS = 50;
export const APP_FADE_MS = 120;

/** The three lines that type on. Deliberately things that are true by then. */
export const CHECK_LINES = ['core online', 'voice pipeline ready', 'link established'] as const;
export const CHECK_LINE_COUNT = CHECK_LINES.length;

/** sessionStorage key: the sequence plays once per browser session, not per navigation. */
export const BOOT_SESSION_KEY = 'jarvis:boot-played';

// --- easings ----------------------------------------------------------------

export function clamp01(v: number): number {
	if (!Number.isFinite(v)) return 0;
	return v < 0 ? 0 : v > 1 ? 1 : v;
}

/** `1 - (1-t)^(2f)` — the shape Android's DecelerateInterpolator draws. */
export function decelerate(t: number, factor = 1): number {
	const p = clamp01(t);
	return 1 - Math.pow(1 - p, 2 * factor);
}

/** `t^(2f)` — AccelerateInterpolator. */
export function accelerate(t: number, factor = 1): number {
	const p = clamp01(t);
	return Math.pow(p, 2 * factor);
}

/**
 * OvershootInterpolator. Exceeds 1 near the end and settles back — callers must
 * expect a value above 1 and scale a radius by it.
 */
export function overshoot(t: number, tension = RING_TENSION): number {
	const p = clamp01(t) - 1;
	return p * p * ((tension + 1) * p + tension) + 1;
}

/** Progress through a window, clamped. A zero-length window is instantly done. */
export function progress(tMs: number, startMs: number, durationMs: number): number {
	if (durationMs <= 0) return tMs >= startMs ? 1 : 0;
	return clamp01((tMs - startMs) / durationMs);
}

// --- stages -----------------------------------------------------------------

/** Which stage `tMs` falls in. Before 0 is `scan`; at or past the end, `handoff`. */
export function stageAt(tMs: number): BootStage {
	for (const s of STAGES) if (tMs < s.endMs) return s.stage;
	return 'handoff';
}

/** How far through its stage `tMs` is, 0..1. */
export function stageProgress(tMs: number, stage: BootStage): number {
	const s = STAGES.find((w) => w.stage === stage);
	if (!s) return 0;
	return progress(tMs, s.startMs, s.endMs - s.startMs);
}

// --- elements ---------------------------------------------------------------

/** Scan line position, 0 = top edge, 1 = bottom edge. */
export function scanY(tMs: number): number {
	return decelerate(progress(tMs, SCAN_START_MS, IGNITE_START_MS), 0.7);
}

/** Scan line opacity: solid through the sweep, gone shortly after ignition. */
export function scanAlpha(tMs: number): number {
	if (tMs <= IGNITE_START_MS) return 1;
	return 1 - progress(tMs, IGNITE_START_MS, SCAN_FADE_MS);
}

/** Core radius as a fraction of its resting radius. Starts at a point. */
export function coreScale(tMs: number): number {
	if (tMs < IGNITE_START_MS) return 0;
	return decelerate(progress(tMs, IGNITE_START_MS, CORE_RISE_MS), 1.8);
}

/** Core opacity. Rises faster than the scale, so it reads as a spark. */
export function coreAlpha(tMs: number): number {
	if (tMs < IGNITE_START_MS) return 0;
	return decelerate(progress(tMs, IGNITE_START_MS, CORE_FADE_MS), 1.4);
}

/** The one-shot bloom around the ignition, peaking mid-flare. */
export function flareAlpha(tMs: number): number {
	if (tMs < IGNITE_START_MS) return 0;
	const p = progress(tMs, IGNITE_START_MS, FLARE_MS);
	if (p >= 1) return 0;
	return 4 * p * (1 - p);
}

export function ringStartMs(index: number): number {
	return RINGS_START_MS + index * RING_STAGGER_MS;
}

/** Ring `index` materialising: 0 absent, 1 at rest, briefly above 1 (overshoot). */
export function ringReveal(tMs: number, index: number): number {
	const p = progress(tMs, ringStartMs(index), RING_MS);
	if (p <= 0) return 0;
	if (p >= 1) return 1;
	return overshoot(p);
}

/** Ring opacity — plain fade, no overshoot, so a ring never flickers past full. */
export function ringAlpha(tMs: number, index: number): number {
	return decelerate(progress(tMs, ringStartMs(index), RING_MS), 1.2);
}

export function letterStartMs(index: number): number {
	return WORDMARK_START_MS + index * LETTER_STAGGER_MS;
}

export function letterAlpha(tMs: number, index: number): number {
	return decelerate(progress(tMs, letterStartMs(index), LETTER_MS), 1.3);
}

/** Blur in px for letter `index`: wide and soft, sharpening to zero. */
export function letterBlur(tMs: number, index: number): number {
	return LETTER_BLUR_PX * (1 - decelerate(progress(tMs, letterStartMs(index), LETTER_MS), 1.3));
}

/**
 * Wordmark letter spacing in em: settles from wide to rest across the whole
 * wordmark stage, so the word closes up as the last letters land.
 */
export function letterSpacing(tMs: number): number {
	const span = letterStartMs(LETTER_COUNT - 1) + LETTER_MS - WORDMARK_START_MS;
	const p = decelerate(progress(tMs, WORDMARK_START_MS, span), 1.6);
	return LETTER_SPACING_START + (LETTER_SPACING_END - LETTER_SPACING_START) * p;
}

export function checkStartMs(index: number): number {
	return CHECKS_START_MS + index * CHECK_STAGGER_MS;
}

export function checkProgress(tMs: number, index: number): number {
	return progress(tMs, checkStartMs(index), CHECK_TYPE_MS);
}

/** Characters of a `length`-character check line visible at `tMs`. */
export function typedChars(tMs: number, index: number, length: number): number {
	if (length <= 0) return 0;
	const n = Math.floor(checkProgress(tMs, index) * length);
	return n > length ? length : n;
}

/** How much of check line `index` to render at `tMs`. */
export function typedLine(tMs: number, index: number): string {
	const line = CHECK_LINES[index] ?? '';
	return line.slice(0, typedChars(tMs, index, line.length));
}

/** Opacity of everything the boot draws. Falls to 0 across the handoff. */
export function chromeAlpha(tMs: number): number {
	return 1 - decelerate(progress(tMs, HANDOFF_START_MS, HANDOFF_FADE_MS), 1.2);
}

/** Opacity of the app fading up underneath the dissolving chrome. */
export function appAlpha(tMs: number): number {
	return decelerate(progress(tMs, HANDOFF_START_MS + APP_FADE_DELAY_MS, APP_FADE_MS), 1.2);
}

// --- whole frames -----------------------------------------------------------

export interface BootFrame {
	stage: BootStage;
	scanY: number;
	scanAlpha: number;
	coreScale: number;
	coreAlpha: number;
	flareAlpha: number;
	ringReveal: number[];
	ringAlpha: number[];
	letterAlpha: number[];
	letterBlur: number[];
	letterSpacing: number;
	checkLines: string[];
	chromeAlpha: number;
	appAlpha: number;
}

/** The whole frame at `tMs`, from the same functions every other frame uses. */
export function frameAt(tMs: number): BootFrame {
	const rings = Array.from({ length: RING_COUNT }, (_, i) => i);
	const letters = Array.from({ length: LETTER_COUNT }, (_, i) => i);
	return {
		stage: stageAt(tMs),
		scanY: scanY(tMs),
		scanAlpha: scanAlpha(tMs),
		coreScale: coreScale(tMs),
		coreAlpha: coreAlpha(tMs),
		flareAlpha: flareAlpha(tMs),
		ringReveal: rings.map((i) => ringReveal(tMs, i)),
		ringAlpha: rings.map((i) => ringAlpha(tMs, i)),
		letterAlpha: letters.map((i) => letterAlpha(tMs, i)),
		letterBlur: letters.map((i) => letterBlur(tMs, i)),
		letterSpacing: letterSpacing(tMs),
		checkLines: CHECK_LINES.map((_, i) => typedLine(tMs, i)),
		chromeAlpha: chromeAlpha(tMs),
		appAlpha: appAlpha(tMs)
	};
}

/** The finished frame. `skip()` jumps here, which is why the two look identical. */
export function endFrame(): BootFrame {
	return frameAt(TOTAL_MS);
}

/**
 * Whether the sequence must not play at all: the user asked for reduced motion,
 * or this session has already seen it. Trapping someone in an animation they
 * turned off is not a flourish, it is a bug.
 */
export function shouldSkipBoot(opts: { reducedMotion: boolean; alreadyPlayed: boolean }): boolean {
	return opts.reducedMotion === true || opts.alreadyPlayed === true;
}

/** Read the "already played this session" flag without throwing on a locked-down storage. */
export function bootAlreadyPlayed(storage?: Pick<Storage, 'getItem'> | null): boolean {
	try {
		return storage?.getItem(BOOT_SESSION_KEY) === '1';
	} catch {
		return false;
	}
}

/** Record that the sequence has played. Never throws. */
export function markBootPlayed(storage?: Pick<Storage, 'setItem'> | null): void {
	try {
		storage?.setItem(BOOT_SESSION_KEY, '1');
	} catch {
		/* private mode, quota, disabled storage — the boot simply plays again */
	}
}
