/**
 * The numbers a widget draws, and the shapes it draws them in.
 *
 * The one rule inherited from the server: **never invent a point**. A gap
 * arrives as `null` and stays a gap — the path breaks rather than drawing a
 * straight line through a period nothing was recorded in, because that line is
 * a claim about time that never happened.
 */

export interface SeriesPoint {
	at: number;
	value: number | null;
}

export interface SeriesData {
	key: string;
	label: string;
	unit: string;
	aggregate: string;
	error: string;
	points: SeriesPoint[];
}

export function toSeries(raw: unknown): SeriesData[] {
	const list = (raw as { series?: unknown })?.series;
	return (Array.isArray(list) ? list : []).map((entry) => {
		const row = (entry ?? {}) as Record<string, unknown>;
		const pairs = Array.isArray(row.points) ? row.points : [];
		return {
			key: String(row.key ?? ''),
			label: String(row.label ?? row.key ?? ''),
			unit: String(row.unit ?? ''),
			aggregate: String(row.aggregate ?? ''),
			error: String(row.error ?? ''),
			points: pairs
				.map((pair) => {
					const [at, value] = Array.isArray(pair) ? pair : [0, null];
					return {
						at: typeof at === 'number' ? at : 0,
						value: typeof value === 'number' && Number.isFinite(value) ? value : null
					};
				})
				.filter((point) => point.at > 0)
		};
	});
}

export interface Extent {
	min: number;
	max: number;
	from: number;
	to: number;
}

/** The bounds a chart needs, over every series it is drawing. */
export function extentOf(series: SeriesData[]): Extent | null {
	const values: number[] = [];
	const times: number[] = [];
	for (const one of series) {
		for (const point of one.points) {
			times.push(point.at);
			if (point.value !== null) values.push(point.value);
		}
	}
	if (!values.length || !times.length) return null;
	const min = Math.min(...values);
	const max = Math.max(...values);
	return {
		// A flat series still needs height, or it draws on the axis and reads as
		// zero. Give it a band around the value it actually has.
		min: min === max ? min - Math.max(1, Math.abs(min) * 0.1) : min,
		max: min === max ? max + Math.max(1, Math.abs(max) * 0.1) : max,
		from: Math.min(...times),
		to: Math.max(...times)
	};
}

/**
 * SVG path segments for one series, in a 0..width / 0..height box.
 *
 * Returns several subpaths, one per run of real points, so a gap breaks the
 * line instead of being drawn through.
 */
export function pathFor(
	series: SeriesData,
	extent: Extent,
	width: number,
	height: number
): string {
	const spanX = Math.max(1e-9, extent.to - extent.from);
	const spanY = Math.max(1e-9, extent.max - extent.min);
	const x = (at: number) => ((at - extent.from) / spanX) * width;
	const y = (value: number) => height - ((value - extent.min) / spanY) * height;

	let path = '';
	let open = false;
	for (const point of series.points) {
		if (point.value === null) {
			open = false;
			continue;
		}
		const command = open ? 'L' : 'M';
		path += `${command}${x(point.at).toFixed(2)} ${y(point.value).toFixed(2)} `;
		open = true;
	}
	return path.trim();
}

/** The same, closed to the baseline, for an area. */
export function areaPathFor(
	series: SeriesData,
	extent: Extent,
	width: number,
	height: number
): string {
	const real = series.points.filter((point) => point.value !== null);
	if (real.length < 2) return '';
	const line = pathFor({ ...series, points: real }, extent, width, height);
	if (!line) return '';
	const spanX = Math.max(1e-9, extent.to - extent.from);
	const x = (at: number) => ((at - extent.from) / spanX) * width;
	const first = x(real[0].at).toFixed(2);
	const last = x(real[real.length - 1].at).toFixed(2);
	return `${line} L${last} ${height} L${first} ${height} Z`;
}

/** The latest real value, which is what a stat and a gauge show. */
export function latest(series: SeriesData | undefined): number | null {
	if (!series) return null;
	for (let i = series.points.length - 1; i >= 0; i--) {
		if (series.points[i].value !== null) return series.points[i].value;
	}
	return null;
}

/** The sum of a series, for a counter drawn as bars. */
export function total(series: SeriesData | undefined): number {
	if (!series) return 0;
	return series.points.reduce((sum, point) => sum + (point.value ?? 0), 0);
}

/** A number a person reads: three significant figures, no exponent soup. */
export function format(value: number | null, unit = ''): string {
	if (value === null) return '—';
	const magnitude = Math.abs(value);
	const text =
		magnitude >= 1000
			? Math.round(value).toLocaleString()
			: magnitude >= 10
				? value.toFixed(1)
				: value.toFixed(2);
	return unit ? `${text} ${unit}` : text;
}
