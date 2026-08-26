/**
 * A dashboard layout, and the arithmetic of moving things around in it.
 *
 * The shape is `tests/contracts/dashboard_layout.json`; jarvis-core's
 * `tests/test_dashboards.py` and `layout.test.ts` read the same file. Pure
 * functions, tested in Node — dragging a widget is exactly the kind of thing
 * that is impossible to reason about inside a component.
 */

import {
	DEFAULT_MOMENTS,
	DEFAULT_SIZE,
	MAX_MOMENTS,
	isChartType,
	isWidgetKind,
	type Aggregate,
	type ChartType,
	type Range,
	type WidgetKind
} from './chartTypes';

/** The grid is twelve columns wide, like the contract says. */
export const COLUMNS = 12;
export const MAX_WIDGETS = 40;

/**
 * One widget. Every field is present whatever the kind, with the other kinds'
 * fields empty, so a component can read `widget.entity` without a guard; the
 * server keeps only the fields the kind needs, and `toWidget` fills the rest
 * back in when the layout comes down.
 */
export interface Widget {
	id: string;
	title: string;
	kind: WidgetKind;
	/** metric */
	type: ChartType;
	source: string;
	series: string[];
	aggregate: Aggregate | '';
	/** entity: the entity_id the tile shows and switches. */
	entity: string;
	/** camera: its name; empty means the only camera, if there is one. */
	camera: string;
	/** readings: one room, or every room when empty. */
	area: string;
	/** moments: how many, newest first. */
	limit: number;
	x: number;
	y: number;
	w: number;
	h: number;
}

/** `domain.object_id`, as the state machine spells it — the server's rule. */
const ENTITY_ID = /^[a-z_]+\.[a-z0-9_]+$/;

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

/**
 * One widget from the wire, or null if it is not one.
 *
 * Refusing rather than drawing: a widget of a kind or chart type this console
 * cannot draw would be a blank card somebody has to delete, a graph with no
 * series an empty chart that looks like a broken sensor, an entity tile with
 * no entity a card about nothing. A widget with no `kind` is a graph — every
 * layout saved before M63 — and a graph with no `type` is a line, as the
 * server has always read one.
 */
export function toWidget(raw: unknown, index = 0): Widget | null {
	if (!raw || typeof raw !== 'object') return null;
	const source = raw as Record<string, unknown>;
	const kind = source.kind === undefined || source.kind === '' ? 'metric' : source.kind;
	if (!isWidgetKind(kind)) return null;
	const size = DEFAULT_SIZE[kind];
	const widget: Widget = {
		id: String(source.id || `w${index}`),
		title: String(source.title || ''),
		kind,
		type: 'line',
		source: 'internal',
		series: [],
		aggregate: '',
		entity: '',
		camera: '',
		area: '',
		limit: DEFAULT_MOMENTS,
		x: clamp(source.x, 0, COLUMNS - 1, 0),
		y: clamp(source.y, 0, 500, index),
		w: clamp(source.w, 1, COLUMNS, size.w),
		h: clamp(source.h, 1, 12, size.h)
	};
	if (kind === 'metric') {
		const type = source.type === undefined || source.type === '' ? 'line' : source.type;
		if (!isChartType(type)) return null;
		const series = (Array.isArray(source.series) ? source.series : [])
			.map((key) => String(key))
			.filter(Boolean)
			.slice(0, 8);
		if (!series.length) return null;
		widget.type = type;
		widget.source = String(source.source || 'internal');
		widget.series = series;
		widget.aggregate = (source.aggregate as Aggregate) || '';
	} else if (kind === 'entity') {
		const entity = String(source.entity || '').trim();
		if (!ENTITY_ID.test(entity)) return null;
		widget.entity = entity;
	} else if (kind === 'camera') {
		widget.camera = String(source.camera || '').trim();
	} else if (kind === 'readings') {
		widget.area = String(source.area || '').trim();
	} else if (kind === 'moments') {
		widget.limit = clamp(source.limit, 1, MAX_MOMENTS, DEFAULT_MOMENTS);
	}
	return widget;
}

/**
 * What the server is sent: only the fields the kind needs. The server drops
 * the rest anyway; sending a graph's empty `series` on an entity tile would
 * only make the wire lie about what the tile is.
 */
export function wireWidget(widget: Widget): Record<string, unknown> {
	const base = {
		id: widget.id,
		title: widget.title,
		kind: widget.kind,
		x: widget.x,
		y: widget.y,
		w: widget.w,
		h: widget.h
	};
	switch (widget.kind) {
		case 'metric':
			return {
				...base,
				type: widget.type,
				source: widget.source,
				series: widget.series,
				aggregate: widget.aggregate
			};
		case 'entity':
			return { ...base, entity: widget.entity };
		case 'camera':
			return { ...base, camera: widget.camera };
		case 'readings':
			return { ...base, area: widget.area };
		case 'moments':
			return { ...base, limit: widget.limit };
		default:
			return base;
	}
}

/** A blank widget of one kind, for the editor's draft. */
export function blankWidget(kind: WidgetKind, id: string): Widget {
	return {
		id,
		title: '',
		kind,
		type: 'line',
		source: 'internal',
		series: [],
		aggregate: '',
		entity: '',
		camera: '',
		area: '',
		limit: DEFAULT_MOMENTS,
		x: 0,
		y: 0,
		...DEFAULT_SIZE[kind]
	};
}

/** What a widget is about, for its header and its accessible name. */
export function widgetSubject(widget: Widget): string {
	if (widget.title) return widget.title;
	switch (widget.kind) {
		case 'metric':
			return widget.series[0] ?? 'graph';
		case 'entity':
			return widget.entity;
		case 'camera':
			return widget.camera || 'camera';
		case 'readings':
			return widget.area ? `readings · ${widget.area}` : 'readings';
		case 'sky':
			return 'tonight';
		case 'moments':
			return 'moments';
	}
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
