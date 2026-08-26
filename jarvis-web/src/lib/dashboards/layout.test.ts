// A layout is the one piece of state a user builds by hand, so the failure
// worth preventing is one side writing something the other refuses.
// `tests/contracts/dashboard_layout.json` is the table; jarvis-core's
// `tests/test_dashboards.py` reads the same file.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
	CHART_TYPE_NAMES,
	DEFAULT_MOMENTS,
	DEFAULT_SIZE,
	MAX_MOMENTS,
	WIDGET_KIND_NAMES,
	isChartType,
	isWidgetKind,
	type WidgetKind
} from './chartTypes';
import {
	COLUMNS,
	addWidget,
	blankWidget,
	moveWidget,
	newWidgetId,
	overlaps,
	placeNew,
	removeWidget,
	resizeWidget,
	sortWidgets,
	swapWidgets,
	toDashboard,
	toDashboards,
	toWidget,
	widgetSubject,
	wireWidget,
	type Widget
} from './layout';

const CONTRACT = JSON.parse(
	readFileSync(
		fileURLToPath(new URL('../../../../tests/contracts/dashboard_layout.json', import.meta.url)),
		'utf8'
	)
);

const widget = (over: Partial<Widget> = {}): Widget => ({
	id: 'w1',
	title: '',
	kind: 'metric',
	type: 'line',
	source: 'internal',
	series: ['host.load1'],
	aggregate: '',
	entity: '',
	camera: '',
	area: '',
	limit: DEFAULT_MOMENTS,
	x: 0,
	y: 0,
	w: 4,
	h: 2,
	...over
});

/** One value per field a kind may need, so any kind can be built from the contract alone. */
const SAMPLE_FIELDS: Record<string, unknown> = {
	type: 'line',
	source: 'internal',
	series: ['a'],
	entity: 'light.hall'
};

const rawOfKind = (kind: string, except = ''): Record<string, unknown> => {
	const needs: string[] = CONTRACT.kinds[kind].needs;
	return Object.fromEntries([
		['kind', kind],
		...needs.filter((field) => field !== except).map((field) => [field, SAMPLE_FIELDS[field]])
	]);
};

describe('the contract', () => {
	it('names exactly the chart types this console can draw', () => {
		expect(new Set(CHART_TYPE_NAMES)).toEqual(new Set(Object.keys(CONTRACT.types)));
	});

	it('agrees about the grid', () => {
		expect(COLUMNS).toBe(CONTRACT.columns);
	});

	it('produces widgets with every required field', () => {
		const cleaned = toWidget({ type: 'line', source: 'internal', series: ['a'] });
		for (const field of CONTRACT.widget.required) expect(cleaned).toHaveProperty(field);
	});

	it('produces dashboards with every required field', () => {
		const board = toDashboard({ id: 'b', title: 'B', widgets: [] });
		for (const field of CONTRACT.dashboard.required) expect(board).toHaveProperty(field);
	});
});

describe('reading a layout', () => {
	it('refuses a chart type nobody can draw', () => {
		expect(isChartType('pie')).toBe(false);
		expect(toWidget({ type: 'pie', series: ['a'] })).toBeNull();
	});

	it('refuses a widget with no series, rather than drawing an empty chart', () => {
		expect(toWidget({ type: 'line', series: [] })).toBeNull();
	});

	it('clamps coordinates into the grid', () => {
		const cleaned = toWidget({ type: 'line', series: ['a'], x: 99, w: 99, h: 0 });
		expect(cleaned?.x).toBe(COLUMNS - 1);
		expect(cleaned?.w).toBe(COLUMNS);
		expect(cleaned?.h).toBeGreaterThanOrEqual(1);
	});

	it('drops the widgets it cannot read and keeps the rest', () => {
		const board = toDashboard({
			id: 'b',
			title: 'B',
			widgets: [{ type: 'pie', series: ['a'] }, { type: 'line', series: ['a'] }]
		});
		expect(board?.widgets).toHaveLength(1);
	});

	it('reads a list, and survives a payload that is not one', () => {
		expect(toDashboards({ dashboards: [{ id: 'a', title: 'A' }] })).toHaveLength(1);
		expect(toDashboards(undefined)).toEqual([]);
		expect(toDashboards({ dashboards: 'nonsense' })).toEqual([]);
	});
});

describe('arranging', () => {
	it('puts a new widget in the first gap, not at the bottom', () => {
		// A dashboard with one half-empty row should fill it rather than growing
		// past the fold.
		const existing = [widget({ id: 'a', x: 0, y: 0, w: 4, h: 2 })];
		expect(placeNew(existing, 4, 2)).toEqual({ x: 4, y: 0 });
	});

	it('knows when two widgets overlap', () => {
		expect(overlaps({ x: 0, y: 0, w: 4, h: 2 }, { x: 2, y: 1, w: 4, h: 2 })).toBe(true);
		expect(overlaps({ x: 0, y: 0, w: 4, h: 2 }, { x: 4, y: 0, w: 4, h: 2 })).toBe(false);
	});

	it('moves a widget without letting it leave the grid', () => {
		const moved = moveWidget([widget()], 'w1', 99, 3);
		expect(moved[0].x).toBe(COLUMNS - 4);
		expect(moved[0].y).toBe(3);
	});

	it('resizes a widget without letting it overflow the grid', () => {
		const resized = resizeWidget([widget({ x: 9, w: 3 })], 'w1', 12, 4);
		expect(resized[0].x + resized[0].w).toBeLessThanOrEqual(COLUMNS);
		expect(resized[0].h).toBe(4);
	});

	it('reorders by swapping, so nothing is left in a gap', () => {
		const before = [widget({ id: 'a', x: 0, y: 0 }), widget({ id: 'b', x: 4, y: 0 })];
		const after = swapWidgets(before, 'a', 'b');
		expect(after.find((w) => w.id === 'a')?.x).toBe(4);
		expect(after.find((w) => w.id === 'b')?.x).toBe(0);
	});

	it('leaves a swap with itself alone', () => {
		const before = [widget({ id: 'a' })];
		expect(swapWidgets(before, 'a', 'a')).toBe(before);
	});

	it('adds and removes', () => {
		let widgets: Widget[] = [];
		widgets = addWidget(widgets, { ...widget(), id: newWidgetId(widgets) } as never);
		expect(widgets).toHaveLength(1);
		widgets = removeWidget(widgets, widgets[0].id);
		expect(widgets).toHaveLength(0);
	});

	it('sorts into reading order', () => {
		const out = sortWidgets([widget({ id: 'b', x: 4, y: 1 }), widget({ id: 'a', x: 0, y: 0 })]);
		expect(out.map((w) => w.id)).toEqual(['a', 'b']);
	});

	it('never reuses an id', () => {
		const widgets = [widget({ id: 'w1' }), widget({ id: 'w2' })];
		expect(newWidgetId(widgets)).toBe('w3');
	});
});

describe('kinds (M63)', () => {
	it('names exactly the kinds the contract names', () => {
		expect(new Set(WIDGET_KIND_NAMES)).toEqual(new Set(Object.keys(CONTRACT.kinds)));
		expect(isWidgetKind('weather')).toBe(false);
	});

	it('reads a widget with no kind as a graph, so a layout saved before kinds still loads', () => {
		const old = toWidget({ id: 'w1', type: 'stat', source: 'internal', series: ['host.disk_free'], x: 0, y: 0, w: 3, h: 2 });
		expect(old?.kind).toBe('metric');
		expect(old?.series).toEqual(['host.disk_free']);
	});

	it('reads a graph with no type as a line, as the server always has', () => {
		expect(toWidget({ series: ['a'] })?.type).toBe('line');
	});

	it('cleans each kind with the fields the contract says it needs, and refuses one missing them', () => {
		for (const kind of Object.keys(CONTRACT.kinds)) {
			const cleaned = toWidget(rawOfKind(kind));
			expect(cleaned, `a ${kind} widget with everything it needs was refused`).not.toBeNull();
			for (const field of [...CONTRACT.widget.required, ...CONTRACT.kinds[kind].needs]) {
				expect(cleaned, `${kind} lacks ${field}`).toHaveProperty(field);
			}
			for (const missing of CONTRACT.kinds[kind].needs) {
				expect(toWidget(rawOfKind(kind, missing)), `${kind} without ${missing} was kept`).toBeNull();
			}
		}
	});

	it('refuses a kind nobody can draw', () => {
		expect(toWidget({ kind: 'weather' })).toBeNull();
	});

	it('needs an entity id the state machine could hold on an entity tile', () => {
		expect(toWidget({ kind: 'entity', entity: 'hall lamp' })).toBeNull();
		expect(toWidget({ kind: 'entity', entity: 'light.' })).toBeNull();
		expect(toWidget({ kind: 'entity', entity: 'light.hall_lamp' })?.entity).toBe('light.hall_lamp');
	});

	it('lets a camera widget leave the camera unnamed — the only one, if there is one', () => {
		expect(toWidget({ kind: 'camera' })?.camera).toBe('');
		expect(toWidget({ kind: 'camera', camera: 'Front Door' })?.camera).toBe('Front Door');
	});

	it('keeps a moments widget a glance, not the inbox', () => {
		expect(toWidget({ kind: 'moments' })?.limit).toBe(DEFAULT_MOMENTS);
		expect(toWidget({ kind: 'moments', limit: 500 })?.limit).toBe(MAX_MOMENTS);
		expect(toWidget({ kind: 'moments', limit: 0 })?.limit).toBe(1);
	});

	it('gives each kind its own footprint when the layout sent none', () => {
		for (const kind of Object.keys(DEFAULT_SIZE) as WidgetKind[]) {
			const cleaned = toWidget(rawOfKind(kind));
			expect({ w: cleaned?.w, h: cleaned?.h }, kind).toEqual(DEFAULT_SIZE[kind]);
			expect(blankWidget(kind, 'w9')).toMatchObject({ id: 'w9', kind, ...DEFAULT_SIZE[kind] });
		}
	});

	it('sends the server only the fields a kind needs', () => {
		const tile = wireWidget(widget({ kind: 'entity', entity: 'light.x', series: ['leak'] }));
		expect(tile).not.toHaveProperty('series');
		expect(tile).toMatchObject({ kind: 'entity', entity: 'light.x' });
		const graph = wireWidget(widget({ entity: 'leak' }));
		expect(graph).not.toHaveProperty('entity');
		expect(graph).toMatchObject({ kind: 'metric', series: ['host.load1'], type: 'line' });
		// And what it sends, it can read back.
		expect(toWidget(tile)).toMatchObject({ kind: 'entity', entity: 'light.x' });
	});

	it('names what a widget is about when it has no title', () => {
		expect(widgetSubject(widget({ title: 'Load' }))).toBe('Load');
		expect(widgetSubject(widget())).toBe('host.load1');
		expect(widgetSubject(widget({ kind: 'entity', entity: 'light.x' }))).toBe('light.x');
		expect(widgetSubject(widget({ kind: 'readings', area: 'Kitchen' }))).toBe('readings · Kitchen');
		expect(widgetSubject(widget({ kind: 'camera' }))).toBe('camera');
		expect(widgetSubject(widget({ kind: 'sky' }))).toBe('tonight');
		expect(widgetSubject(widget({ kind: 'moments' }))).toBe('moments');
	});
});
