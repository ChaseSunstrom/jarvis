/**
 * The chart types a widget may be, and what each one is FOR.
 *
 * The list is not a menu of shapes: picking a chart is picking a question, and
 * a widget that draws the wrong one lies quietly. `when` is the sentence shown
 * in the picker, so the person arranging a dashboard chooses by question.
 *
 * The types here and the ones jarvis-core accepts are the same list —
 * `tests/contracts/dashboard_layout.json` is the table, and both sides' tests
 * read it.
 */

export const CHART_TYPES = {
	line: {
		label: 'Line',
		when: 'A level over time: temperature, load, tokens per second.',
		/** More than one series on one chart makes sense. */
		multi: true,
		/** Needs a time axis. */
		temporal: true
	},
	area: {
		label: 'Area',
		when: 'A quantity over time, where the area under it means something: power, throughput.',
		multi: false,
		temporal: true
	},
	bar: {
		label: 'Bars',
		when: 'How many, per bucket: tasks a day, calls an hour.',
		multi: false,
		temporal: true
	},
	stat: {
		label: 'Number',
		when: 'One value, large, with its recent shape behind it.',
		multi: false,
		temporal: false
	},
	gauge: {
		label: 'Gauge',
		when: 'One value against a maximum: disk used, memory, a percentage.',
		multi: false,
		temporal: false
	},
	table: {
		label: 'Table',
		when: 'Several series’ latest values side by side.',
		multi: true,
		temporal: false
	}
} as const;

export type ChartType = keyof typeof CHART_TYPES;

export const CHART_TYPE_NAMES = Object.keys(CHART_TYPES) as ChartType[];

export function isChartType(value: unknown): value is ChartType {
	return typeof value === 'string' && value in CHART_TYPES;
}

/**
 * What a widget SHOWS (M63) — a graph is one kind among six.
 *
 * `metric` is the graphs of M62 and what a widget with no `kind` is, so a
 * layout saved before kinds existed loads unchanged. The rest show the house
 * rather than a number. `when` is the sentence in the picker, so a person
 * chooses by what they want to see; `needs` names the one field the editor
 * has to ask for, or nothing.
 *
 * The list and jarvis-core's are the same list — the contract's `kinds` table,
 * which both suites read.
 */
export const WIDGET_KINDS = {
	metric: {
		label: 'Graph',
		when: 'Recorded numbers over time: a temperature, a load, a count.',
		needs: 'series'
	},
	entity: {
		label: 'Entity',
		when: 'One thing in the house: its state, when it changed, and its switch.',
		needs: 'entity'
	},
	readings: {
		label: 'Readings',
		when: 'The newest sensor readings, room by room.',
		needs: 'area'
	},
	camera: {
		label: 'Camera',
		when: 'A still from a camera, on the camera’s own consent terms.',
		needs: 'camera'
	},
	sky: {
		label: 'Sky',
		when: 'The next ISS pass over the house and the moon tonight.',
		needs: ''
	},
	moments: {
		label: 'Moments',
		when: 'What Jarvis said while nobody was looking, newest first.',
		needs: ''
	}
} as const;

export type WidgetKind = keyof typeof WIDGET_KINDS;

export const WIDGET_KIND_NAMES = Object.keys(WIDGET_KINDS) as WidgetKind[];

export function isWidgetKind(value: unknown): value is WidgetKind {
	return typeof value === 'string' && value in WIDGET_KINDS;
}

/** A new widget's footprint per kind, in columns and rows — the server's table. */
export const DEFAULT_SIZE: Record<WidgetKind, { w: number; h: number }> = {
	metric: { w: 4, h: 2 },
	entity: { w: 3, h: 2 },
	readings: { w: 6, h: 3 },
	camera: { w: 6, h: 3 },
	sky: { w: 3, h: 2 },
	moments: { w: 6, h: 3 }
};

/** How many moments a moments widget shows when it does not say, and the most. */
export const DEFAULT_MOMENTS = 6;
export const MAX_MOMENTS = 20;

/** How several samples inside one step become one point. */
export const AGGREGATES = ['last', 'mean', 'min', 'max', 'sum', 'count'] as const;
export type Aggregate = (typeof AGGREGATES)[number];

/**
 * Sources the console knows how to talk about in the picker.
 *
 * The list is not a gate — a backend may serve any source name and the picker
 * shows whatever it is told — it is what to SAY about the two that ship, so an
 * unconfigured InfluxDB reads as "not set up yet" rather than as broken.
 */
export const SOURCE_NOTES: Record<string, string> = {
	internal: 'Jarvis itself: entity history, this host, and the assistant’s own work.',
	influx: 'An InfluxDB you already run. Configure it under metrics: sources: influx.'
};

/** The windows the range switch offers. */
export const RANGES = ['1h', '6h', '24h', '7d'] as const;
export type Range = (typeof RANGES)[number];
