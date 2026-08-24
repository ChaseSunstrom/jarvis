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

/** How several samples inside one step become one point. */
export const AGGREGATES = ['last', 'mean', 'min', 'max', 'sum', 'count'] as const;
export type Aggregate = (typeof AGGREGATES)[number];

/** The windows the range switch offers. */
export const RANGES = ['1h', '6h', '24h', '7d'] as const;
export type Range = (typeof RANGES)[number];
