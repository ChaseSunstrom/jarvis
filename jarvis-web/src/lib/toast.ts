// Toasts.
//
// Before this existed a `call_service` that the backend rejected set a string on
// whichever page issued it — or, from the command palette, went nowhere at all.
// A failed unlock deserves louder than that.
//
// Deliberately not a Svelte store: a plain observable keeps it testable in node
// and usable from modules that are not components (the palette's action
// dispatcher, the console link).

export type ToastKind = 'success' | 'error' | 'info';

export interface Toast {
	id: number;
	kind: ToastKind;
	text: string;
	/** Second line: the error code, the entity id. */
	detail?: string;
}

/** How long a toast lives, by kind. Failures stay up longer — they matter more. */
export const TOAST_TTL_MS: Record<ToastKind, number> = {
	success: 3200,
	info: 4000,
	error: 6500
};

/** Most toasts on screen at once; the oldest is dropped past this. */
export const MAX_TOASTS = 4;

export type ToastListener = (toasts: Toast[]) => void;

export class ToastBus {
	private items: Toast[] = [];
	private listeners = new Set<ToastListener>();
	private timers = new Map<number, ReturnType<typeof setTimeout>>();
	private seq = 0;

	constructor(private max = MAX_TOASTS) {}

	/** The current toasts, oldest first. A copy — callers cannot mutate the bus. */
	get list(): Toast[] {
		return [...this.items];
	}

	subscribe(listener: ToastListener): () => void {
		this.listeners.add(listener);
		listener(this.list);
		return () => this.listeners.delete(listener);
	}

	/** Show a toast. `ttl = 0` pins it until it is dismissed. */
	push(kind: ToastKind, text: string, detail?: string, ttl = TOAST_TTL_MS[kind]): number {
		const id = ++this.seq;
		this.items = [...this.items, { id, kind, text, detail }];
		while (this.items.length > this.max) {
			const dropped = this.items[0];
			this.items = this.items.slice(1);
			this.clearTimer(dropped.id);
		}
		if (ttl > 0) {
			const timer = setTimeout(() => this.dismiss(id), ttl);
			(timer as any)?.unref?.();
			this.timers.set(id, timer);
		}
		this.emit();
		return id;
	}

	success(text: string, detail?: string): number {
		return this.push('success', text, detail);
	}
	error(text: string, detail?: string): number {
		return this.push('error', text, detail);
	}
	info(text: string, detail?: string): number {
		return this.push('info', text, detail);
	}

	dismiss(id: number): void {
		const before = this.items.length;
		this.items = this.items.filter((t) => t.id !== id);
		this.clearTimer(id);
		if (this.items.length !== before) this.emit();
	}

	clear(): void {
		for (const id of [...this.timers.keys()]) this.clearTimer(id);
		if (!this.items.length) return;
		this.items = [];
		this.emit();
	}

	private clearTimer(id: number): void {
		const timer = this.timers.get(id);
		if (timer !== undefined) clearTimeout(timer);
		this.timers.delete(id);
	}

	private emit(): void {
		const snapshot = this.list;
		for (const listener of this.listeners) listener(snapshot);
	}
}

/** The app-wide bus. One per page load; pages and the palette both write to it. */
export const toasts = new ToastBus();

/** Sentence-case a service name for a toast: `turn_on` -> `Turn on`. */
export function humanizeService(service: string): string {
	const words = String(service ?? '').replace(/_/g, ' ').trim();
	if (!words) return 'Call';
	return words[0].toUpperCase() + words.slice(1);
}

/** The success line for a service call, e.g. `Turn on · Lab Lights`. */
export function serviceSuccessText(service: string, label: string): string {
	return `${humanizeService(service)} · ${label}`;
}

/** The failure line. The backend's own message goes in the detail slot. */
export function serviceFailureText(service: string, label: string): string {
	return `${humanizeService(service)} failed · ${label}`;
}
