// Not throwing away what somebody typed.
//
// Every editor in the console is a panel that drops out of a row, and every one
// of them opened by reassigning the form: click MANAGE on another row — or on
// the same one, to close it — and a half-written name, a rewritten trigger, a
// JSON body somebody had just got right, were gone with no warning and no way
// back. The form is the only copy; the server has never seen it.
//
// The rule here is the one the delete buttons already use: the second press
// does it. The first press says what is about to be lost, in a toast, and arms
// a short window; a press inside that window goes through. No modal, because a
// native `confirm()` blocks the whole tab, and no "are you sure" panel, because
// the thing being protected is worth about four seconds of attention.

/** How long a "press again to discard" stays armed. */
export const DISCARD_WINDOW_MS = 4000;

/**
 * Whether two form snapshots hold different values.
 *
 * Shallow and stringly, which is exactly what these forms are: every field of
 * `DraftForm` and `ToolForm` is a string, because a `<textarea>` holding JSON
 * is a string until it is parsed. Comparing the parsed forms instead would call
 * two spellings of the same JSON identical — and one of them is the one the
 * person is still typing.
 */
export function formsDiffer<T extends Record<string, unknown>>(a: T, b: T): boolean {
	const keys = new Set([...Object.keys(a ?? {}), ...Object.keys(b ?? {})]);
	for (const key of keys) {
		if (a?.[key] !== b?.[key]) return true;
	}
	return false;
}

/**
 * The two-press window in front of an editor that has unsaved edits.
 *
 * Holds no Svelte state on purpose: nothing renders it, the toast does the
 * talking, and a plain object is testable in node — which is where the timing
 * rules below are pinned rather than in a browser.
 */
export class DiscardGuard {
	private armed = '';
	private timer: ReturnType<typeof setTimeout> | undefined;

	/**
	 * @param warn Called with the target of the press that was refused, so the
	 *   caller can say what is about to be lost in its own words.
	 */
	constructor(
		private warn: (target: string) => void,
		private windowMs = DISCARD_WINDOW_MS
	) {}

	/**
	 * True when the press may go through.
	 *
	 * A clean editor always may. A dirty one may on the second press at the SAME
	 * target: arming per target means "close this" and "open that" cannot be
	 * confused for each other, so a stray second click somewhere else re-asks
	 * rather than discarding.
	 */
	allows(target: string, dirty: boolean): boolean {
		if (!dirty) {
			this.reset();
			return true;
		}
		if (this.armed === target) {
			this.reset();
			return true;
		}
		this.armed = target;
		this.clearTimer();
		this.timer = setTimeout(() => {
			this.armed = '';
			this.timer = undefined;
		}, this.windowMs);
		(this.timer as any)?.unref?.();
		this.warn(target);
		return false;
	}

	/** Whether a second press is currently armed, and for what. */
	get pending(): string {
		return this.armed;
	}

	reset(): void {
		this.armed = '';
		this.clearTimer();
	}

	private clearTimer(): void {
		if (this.timer !== undefined) clearTimeout(this.timer);
		this.timer = undefined;
	}
}
