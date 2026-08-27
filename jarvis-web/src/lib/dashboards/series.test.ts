// The rule inherited from the server: never invent a point. A gap must break
// the line, because a line drawn through a period nothing was recorded in is a
// claim about time that never happened.
import { describe, it, expect } from 'vitest';
import {
	areaPathFor,
	extentOf,
	format,
	latest,
	pathFor,
	toSeries,
	total,
	type SeriesData
} from './series';

const series = (points: [number, number | null][]): SeriesData => ({
	key: 'k',
	label: 'K',
	unit: '',
	aggregate: 'mean',
	error: '',
	points: points.map(([at, value]) => ({ at, value }))
});

describe('reading the wire', () => {
	it('reads [at, value] pairs and keeps nulls as gaps', () => {
		const read = toSeries({ series: [{ key: 'k', points: [[1, 2], [2, null]] }] });
		expect(read[0].points).toEqual([
			{ at: 1, value: 2 },
			{ at: 2, value: null }
		]);
	});

	it('drops a point with no time rather than drawing it at the epoch', () => {
		const read = toSeries({ series: [{ key: 'k', points: [[0, 5], [1, 6]] }] });
		expect(read[0].points).toHaveLength(1);
	});

	it('carries an error through, so the widget can say why it is empty', () => {
		expect(toSeries({ series: [{ key: 'k', error: 'unreachable' }] })[0].error).toBe(
			'unreachable'
		);
	});
});

describe('the extent', () => {
	it('gives a flat series height, so it does not read as zero', () => {
		const extent = extentOf([series([[1, 5], [2, 5]])])!;
		expect(extent.min).toBeLessThan(5);
		expect(extent.max).toBeGreaterThan(5);
	});

	it('is null when there is nothing to draw', () => {
		expect(extentOf([series([[1, null]])])).toBeNull();
		expect(extentOf([])).toBeNull();
	});
});

describe('the path', () => {
	it('breaks at a gap instead of drawing through it', () => {
		const one = series([[1, 1], [2, null], [3, 3]]);
		const extent = extentOf([one])!;
		const path = pathFor(one, extent, 100, 50);
		// Two subpaths: one before the gap, one after.
		expect(path.match(/M/g)).toHaveLength(2);
	});

	it('closes an area to the baseline', () => {
		const one = series([[1, 1], [2, 3]]);
		const path = areaPathFor(one, extentOf([one])!, 100, 50);
		expect(path.endsWith('Z')).toBe(true);
	});

	it('draws no area for a single point, which would be a triangle of nothing', () => {
		const one = series([[1, 1]]);
		expect(areaPathFor(one, extentOf([one])!, 100, 50)).toBe('');
	});
});

describe('the numbers a person reads', () => {
	it('takes the latest REAL value, not the latest point', () => {
		expect(latest(series([[1, 5], [2, null]]))).toBe(5);
		expect(latest(series([[1, null]]))).toBeNull();
	});

	it('sums a counter', () => {
		expect(total(series([[1, 2], [2, null], [3, 3]]))).toBe(5);
	});

	it('formats without exponent soup, and says “—” for nothing', () => {
		expect(format(null)).toBe('—');
		expect(format(1234.5678)).toBe('1,235');
		expect(format(21.44, '°C')).toBe('21.4 °C');
		expect(format(0.5)).toBe('0.50');
	});
});
