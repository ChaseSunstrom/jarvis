/**
 * A dashboard layout, and the arithmetic of moving things around in it.
 *
 * The shape is `tests/contracts/dashboard_layout.json`; jarvis-core's
 * `tests/test_dashboards.py` and `layout.test.ts` read the same file. Pure
 * functions, tested in Node — dragging a widget is exactly the kind of thing
 * that is impossible to reason about inside a component.
 */

import { isChartType, type Aggregate, type ChartType, type Range } from './chartTypes';

/** The grid is twelve columns wide, like the contract says. */
export const COLUMNS = 12;
export const MAX_WIDGETS = 40;

export interface Widget {
	id: string;
	title: string;
	type: ChartType;
	source: string;
	series: string[];
	aggregate: Aggregate | '';
	x: number;
	y: number;
	w: number;
	h: number;
}

export interface Dashboard {
	id: string;
	title: string;
	owner: string;
	range: Range;
	widgets: Widget[];
	updated: number;
	/** Read-only: it came with Jarvis and belongs to nobody. */
	shipped?: boolean;
}

const clamp = (value: unknown, low: number, high: number, fallback: number): number => {
	const number = typeof value === 'number' ? value : Number(value);
	if (!Number.isFinite(number)) return fallback;
	return Math.max(low, Math.min(high, Math.round(number)));
};

export function toWidget(raw: unknown, index = 0): Widget | null {
	if (!raw || typeof raw !== 'object') return null;
	const source = raw as Record<string, unknown>;
	if (!isChartType(source.type)) return null;
	const series = (Array.isArray(source.series) ? source.series : [])
		.map((key) => String(key))
		.filter(Boolean)
		.slice(0, 8);
	if (!series.length) return null;
	return {
		id: String(source.id || `w${index}`),
		title: String(source.title || ''),
		type: source.type,
		source: String(source.source || 'internal'),
		series,
		aggregate: (source.aggregate as Aggregate) || '',
		x: clamp(source.x, 0, COLUMNS - 1, 0),
		y: clamp(source.y, 0, 500, index),
		w: clamp(source.w, 1, COLUMNS, 4),
		h: clamp(source.h, 1, 12, 2)
	};
}

export function toDashboard(raw: unknown): Dashboard | null {
	if (!raw || typeof raw !== 'object') return null;
	const source = raw as Record<string, unknown>;
	const id = String(source.id || '');
	const title = String(source.title || '');
	if (!id || !title) return null;
	const widgets = (Array.isArray(source.widgets) ? source.widgets : [])
		.map((widget, index) => toWidget(widget, index))
		.filter((widget): widget is Widget => widget !== null)
		.slice(0, MAX_WIDGETS);
	return {
		id,
		title,
		owner: String(source.owner || ''),
		range: (['1h', '6h', '24h', '7d'] as const).includes(source.range as Range)
			? (source.range as Range)
			: '6h',
		widgets,
		updated: typeof source.updated === 'number' ? source.updated : 0,
		shipped: Boolean(source.shipped)
	};
}

export function toDashboards(raw: unknown): Dashboard[] {
	const list = (raw as { dashboards?: unknown })?.dashboards;
	return (Array.isArray(list) ? list : [])
		.map(toDashboard)
		.filter((board): board is Dashboard => board !== null);
}

/** Reading order, which is the order the grid lays them out in. */
export function sortWidgets(widgets: Widget[]): Widget[] {
	return [...widgets].sort((a, b) => a.y - b.y || a.x - b.x);
}

/**
 * Where a new widget goes: the first row with room, at the left.
 *
 * Appending at the bottom would be simpler and wrong — a dashboard with one
 * half-empty row would grow downwards past the fold rather than filling it.
 */
export function placeNew(widgets: Widget[], w = 4, h = 2): { x: number; y: number } {
	const width = clamp(w, 1, COLUMNS, 4);
	for (let y = 0; y < 200; y++) {
		for (let x = 0; x + width <= COLUMNS; x++) {
			const candidate = { x, y, w: width, h: clamp(h, 1, 12, 2) };
			if (!widgets.some((widget) => overlaps(widget, candidate))) return { x, y };
		}
	}
	return { x: 0, y: (widgets.at(-1)?.y ?? 0) + 1 };
}

export function overlaps(a: { x: number; y: number; w: number; h: number }, b: typeof a): boolean {
	return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/** Move one widget to a new slot, pushing nothing: the grid is sparse. */
export function moveWidget(widgets: Widget[], id: string, x: number, y: number): Widget[] {
	return widgets.map((widget) =>
		widget.id === id
			? { ...widget, x: clamp(x, 0, COLUMNS - widget.w, widget.x), y: clamp(y, 0, 500, widget.y) }
			: widget
	);
}

/** Resize, keeping it inside the grid. */
export function resizeWidget(widgets: Widget[], id: string, w: number, h: number): Widget[] {
	return widgets.map((widget) =>
		widget.id === id
			? {
					...widget,
					w: clamp(w, 1, COLUMNS - widget.x, widget.w),
					h: clamp(h, 1, 12, widget.h)
				}
			: widget
	);
}

/**
 * Swap two widgets' positions. This is what "reorder" means on a grid: dragging
 * A onto B puts each where the other was, so nothing is left in a gap and
 * nothing silently overlaps.
 */
export function swapWidgets(widgets: Widget[], a: string, b: string): Widget[] {
	const first = widgets.find((widget) => widget.id === a);
	const second = widgets.find((widget) => widget.id === b);
	if (!first || !second || a === b) return widgets;
	return widgets.map((widget) => {
		if (widget.id === a) return { ...widget, x: second.x, y: second.y };
		if (widget.id === b) return { ...widget, x: first.x, y: first.y };
		return widget;
	});
}

export function removeWidget(widgets: Widget[], id: string): Widget[] {
	return widgets.filter((widget) => widget.id !== id);
}

export function addWidget(widgets: Widget[], widget: Omit<Widget, 'x' | 'y'>): Widget[] {
	const at = placeNew(widgets, widget.w, widget.h);
	return [...widgets, { ...widget, ...at }].slice(0, MAX_WIDGETS);
}

/** A unique id for a new widget, stable enough to key a list by. */
export function newWidgetId(widgets: Widget[]): string {
	let n = widgets.length + 1;
	while (widgets.some((widget) => widget.id === `w${n}`)) n++;
	return `w${n}`;
}
