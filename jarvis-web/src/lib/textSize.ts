// How big the text is, as a user's own choice.
//
// Every size in the design system is expressed in `rem`, so one number on the
// root element moves all of them together — the whole scale, both surfaces, in
// step. That is the only way to do this without a second set of tokens: a
// per-component override would drift the moment anybody added a component.
//
// The stored value is a NAME rather than a number. A number invites a slider,
// a slider invites 1.07, and a console rendered at 1.07 is a console nobody
// tested. Four steps, each one legible, is the whole range.
//
// Deliberately not a Svelte store, for the same reason `toast.ts` is not one:
// it has to be usable from the layout, from a page, and from a test in node.

/** One step of the scale: what it is called, and what it multiplies rem by. */
export interface TextSize {
	id: string;
	label: string;
	/** Root font-size as a percentage of the browser's own default. */
	scale: number;
	/** What the step is for, shown beside the control. */
	note: string;
}

/**
 * The steps.
 *
 * `standard` is 1, which means "whatever this browser's default is" — that
 * default is itself a setting the user may already have raised, and multiplying
 * it is right where overriding it in pixels would be wrong.
 */
export const TEXT_SIZES: readonly TextSize[] = [
	{ id: 'compact', label: 'COMPACT', scale: 0.9, note: 'dense — for a desk monitor up close' },
	{ id: 'standard', label: 'STANDARD', scale: 1, note: 'the browser’s own text size' },
	{ id: 'large', label: 'LARGE', scale: 1.15, note: 'for a wall panel, or across a room' },
	{ id: 'largest', label: 'LARGEST', scale: 1.3, note: 'as large as the layouts hold' }
];

export const DEFAULT_TEXT_SIZE = 'standard';

/** Where the choice is kept. Namespaced like `jarvis.muted`, for the same reason. */
export const TEXT_SIZE_KEY = 'jarvis.textSize';

/** The named step, or the default when the name is unknown. */
export function textSizeFor(id: string | null | undefined): TextSize {
	return TEXT_SIZES.find((s) => s.id === id) ?? TEXT_SIZES.find((s) => s.id === DEFAULT_TEXT_SIZE)!;
}

/** What `readTextSize` and `writeTextSize` need from `localStorage`. */
export interface StorageLike {
	getItem(key: string): string | null;
	setItem(key: string, value: string): void;
}

/**
 * The stored choice, or the default.
 *
 * Storage throws rather than returning null in a browser with cookies blocked
 * and in some private modes, so every access is guarded — a preference that
 * cannot be read must not be able to stop the page rendering.
 */
export function readTextSize(storage: StorageLike | undefined | null): TextSize {
	try {
		return textSizeFor(storage?.getItem(TEXT_SIZE_KEY));
	} catch {
		return textSizeFor(DEFAULT_TEXT_SIZE);
	}
}

/** Remember the choice. Returns the step, so a caller can apply it in one line. */
export function writeTextSize(storage: StorageLike | undefined | null, id: string): TextSize {
	const size = textSizeFor(id);
	try {
		storage?.setItem(TEXT_SIZE_KEY, size.id);
	} catch {
		// Storage is off. The size still applies to this page, which is more
		// useful than refusing to change it at all.
	}
	return size;
}

/** What `applyTextSize` needs from a document. */
export interface RootLike {
	documentElement?: { style?: { fontSize?: string } } | null;
}

/**
 * Put the scale on the root element, as a percentage.
 *
 * A percentage rather than a pixel size: it multiplies whatever the browser (or
 * the operating system, or the user) has already decided a default font is,
 * instead of replacing it — so somebody who has already set their browser to
 * 20px and then picks LARGE here gets 23px, not 18px.
 */
export function applyTextSize(doc: RootLike | undefined | null, size: TextSize): void {
	const style = doc?.documentElement?.style;
	if (!style) return;
	style.fontSize = `${Math.round(size.scale * 100)}%`;
}
