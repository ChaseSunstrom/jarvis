import { describe, expect, it } from 'vitest';
import {
	MIN_EVERY_MINUTES,
	blankScheduleForm,
	describeJob,
	jobFootnote,
	localTimestamp,
	parseScheduleForm,
	readOnlyNote,
	sortJobs,
	until,
	type ScheduledJob
} from './schedule';

function job(over: Partial<ScheduledJob> = {}): ScheduledJob {
	return {
		id: 'j1',
		title: 'Bins',
		kind: 'notify',
		when: { mode: 'daily', at: '19:00', days: [], minutes: 0 },
		describes: 'every day at 19:00',
		payload: {},
		enabled: true,
		next_at: 2000,
		last_at: 0,
		last_result: '',
		missed: 0,
		created: 1,
		source: '',
		editable: true,
		...over
	};
}

describe('a one-off', () => {
	it('is sent as a naive local timestamp, not converted to UTC', () => {
		// `toISOString()` converts using the BROWSER's offset, and the browser
		// may be a laptop in another country from the house. jarvis-core reads
		// a zone-less timestamp in the house's zone, which is the one the user
		// meant when they said "seven".
		expect(localTimestamp('2026-07-01', '19:00')).toBe('2026-07-01T19:00:00');
		expect(localTimestamp('2026-07-01', '19:00:30')).toBe('2026-07-01T19:00:30');
	});

	it('carries no Z and no offset', () => {
		const form = {
			...blankScheduleForm(),
			message: 'x',
			mode: 'once' as const,
			date: '2026-07-01',
			time: '19:00'
		};
		const result = parseScheduleForm(form);
		expect(result.ok).toBe(true);
		if (result.ok) {
			const at = String((result.payload.when as { at: string }).at);
			expect(at).not.toMatch(/Z$/);
			expect(at).not.toMatch(/[+-]\d\d:\d\d$/);
		}
	});

	it('needs a date as well as a time', () => {
		const form = { ...blankScheduleForm(), message: 'x', mode: 'once' as const, time: '19:00' };
		const result = parseScheduleForm(form);
		expect(result.ok).toBe(false);
		if (!result.ok) expect(result.field).toBe('date');
	});
});

describe('a repeat', () => {
	it('sends just the time for a daily job', () => {
		const form = {
			...blankScheduleForm(),
			message: 'x',
			mode: 'daily' as const,
			time: '07:30'
		};
		const result = parseScheduleForm(form);
		if (result.ok) expect(result.payload.when).toEqual({ mode: 'daily', at: '07:30', days: [] });
	});

	it('needs days for a weekly job', () => {
		const form = { ...blankScheduleForm(), message: 'x', mode: 'weekly' as const, time: '09:00' };
		const result = parseScheduleForm(form);
		expect(result.ok).toBe(false);
		if (!result.ok) expect(result.field).toBe('days');
	});

	it('carries the days it was given', () => {
		const form = {
			...blankScheduleForm(),
			message: 'x',
			mode: 'weekly' as const,
			time: '09:00',
			days: ['mon', 'fri']
		};
		const result = parseScheduleForm(form);
		if (result.ok) {
			expect(result.payload.when).toEqual({ mode: 'weekly', at: '09:00', days: ['mon', 'fri'] });
		}
	});

	it('refuses a repeat faster than the server would accept', () => {
		// Mirrors MIN_EVERY_MINUTES. Catching it here means the cursor is still
		// in the field; the server refuses it again regardless.
		const form = { ...blankScheduleForm(), message: 'x', mode: 'every' as const, minutes: '1' };
		const result = parseScheduleForm(form);
		expect(result.ok).toBe(false);
		if (!result.ok) expect(result.error).toContain(String(MIN_EVERY_MINUTES));
	});

	it('accepts one at the floor', () => {
		const form = {
			...blankScheduleForm(),
			message: 'x',
			mode: 'every' as const,
			minutes: String(MIN_EVERY_MINUTES)
		};
		expect(parseScheduleForm(form).ok).toBe(true);
	});
});

describe('what each kind needs', () => {
	it('a reminder needs something to say', () => {
		const result = parseScheduleForm({ ...blankScheduleForm(), mode: 'daily', time: '07:00' });
		expect(result.ok).toBe(false);
		if (!result.ok) expect(result.field).toBe('message');
	});

	it('a research job needs a question', () => {
		const form = {
			...blankScheduleForm(),
			kind: 'research' as const,
			mode: 'daily' as const,
			time: '07:00'
		};
		const result = parseScheduleForm(form);
		expect(result.ok).toBe(false);
		if (!result.ok) expect(result.field).toBe('question');
	});

	it('a service job needs something that looks like a service', () => {
		const base = {
			...blankScheduleForm(),
			kind: 'service' as const,
			mode: 'daily' as const,
			time: '07:00'
		};
		expect(parseScheduleForm({ ...base, service: 'turn_on' }).ok).toBe(false);
		expect(parseScheduleForm({ ...base, service: 'light.turn_on' }).ok).toBe(true);
	});

	it('defaults to a reminder that runs once', () => {
		expect(blankScheduleForm().kind).toBe('notify');
		expect(blankScheduleForm().mode).toBe('once');
	});
});

describe('what a row says', () => {
	it('leads with when it next runs', () => {
		expect(describeJob(job({ next_at: 1000 + 3600 }), 1000)).toBe(
			'every day at 19:00 · next in 1h'
		);
	});

	it('tells a disabled job apart from a spent one', () => {
		// Both have no next firing and they are completely different situations.
		expect(describeJob(job({ enabled: false }))).toBe('off');
		expect(
			describeJob(job({ next_at: null, when: { mode: 'once', at: '', days: [], minutes: 0 } }))
		).toBe('done');
		expect(describeJob(job({ next_at: null }))).toBe('not scheduled');
	});

	it('says how long until, in units somebody reads', () => {
		expect(until(1060, 1000)).toBe('in under a minute');
		expect(until(1000 + 600, 1000)).toBe('in 10m');
		expect(until(1000 + 3600, 1000)).toBe('in 1h');
		expect(until(1000 + 7200, 1000)).toBe('in 2h');
		expect(until(1000 + 3 * 86400, 1000)).toBe('in 3 days');
		expect(until(900, 1000)).toBe('due');
		expect(until(null)).toBe('');
	});

	it('does not round half an hour away', () => {
		// "in 2h" for something due in ninety minutes is half an hour of lie at
		// exactly the range where somebody is deciding whether to wait.
		expect(until(1000 + 5400, 1000)).toBe('in 1h 30m');
		expect(until(1000 + 4500, 1000)).toBe('in 1h 15m');
	});

	it('surfaces what was missed rather than leaving it to be noticed', () => {
		// jarvis-core refuses to replay a backlog, which is right — and a run
		// that quietly did not happen is exactly the thing to be told about.
		expect(jobFootnote(job({ missed: 3 }))).toContain('3 firings missed');
		expect(jobFootnote(job({ missed: 1 }))).toContain('1 firing missed');
		expect(jobFootnote(job({ last_result: 'told you: x' }))).toBe('told you: x');
		expect(jobFootnote(job())).toBe('');
	});

	it('explains a row with no buttons', () => {
		expect(readOnlyNote(job({ editable: false }))).toMatch(/configuration\.yaml/);
		expect(readOnlyNote(job())).toBe('');
	});
});

describe('the order', () => {
	it('is soonest first, with the unscheduled ones last', () => {
		const jobs = [
			job({ id: 'never', next_at: null }),
			job({ id: 'later', next_at: 5000 }),
			job({ id: 'soon', next_at: 2000 })
		];
		expect(sortJobs(jobs).map((j) => j.id)).toEqual(['soon', 'later', 'never']);
	});

	it('does not mutate what it was given', () => {
		const jobs = [job({ id: 'b', next_at: 5000 }), job({ id: 'a', next_at: 1000 })];
		sortJobs(jobs);
		expect(jobs[0].id).toBe('b');
	});
});
