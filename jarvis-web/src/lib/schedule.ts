/**
 * Scheduled jobs, as the console understands them.
 *
 * jarvis-core owns the arithmetic (`integrations/schedule/plan.py`) and sends
 * `describes` already written — "every day at 07:30" — precisely so two
 * surfaces cannot disagree about what a schedule means. Nothing here recomputes
 * that; what is here is the form, and one piece of arithmetic the server cannot
 * do for us because it does not know what time it is *here*.
 *
 * ## The one-shot problem
 *
 * A one-off is entered as a date and a time in the browser's local zone, and it
 * has to reach jarvis-core as an ISO timestamp meaning that same wall-clock
 * moment. `new Date(...).toISOString()` converts to UTC, which is right only if
 * the two agree — and the browser may be a laptop in another country. So the
 * local fields are sent as a NAIVE local timestamp and jarvis-core reads it in
 * the house's own zone, which is the one the user meant when they said "seven".
 */

export type Mode = 'once' | 'daily' | 'weekly' | 'every';
export type JobKind = 'notify' | 'research' | 'service';

export const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;

/** Mirrors `MIN_EVERY_MINUTES` in `integrations/schedule/plan.py`. */
export const MIN_EVERY_MINUTES = 5;

export interface ScheduledJob {
	id: string;
	title: string;
	kind: JobKind;
	when: { mode: Mode; at: string; days: string[]; minutes: number };
	/** Written by jarvis-core so both surfaces say the same thing. */
	describes: string;
	payload: Record<string, unknown>;
	enabled: boolean;
	/** Epoch seconds, or null once a spent one-shot has nowhere left to go. */
	next_at: number | null;
	last_at: number;
	last_result: string;
	missed: number;
	created: number;
	source: string;
	/** False for jobs from configuration.yaml — the file owns those. */
	editable: boolean;
}

export interface ScheduleForm {
	title: string;
	kind: JobKind;
	/** For `notify`. */
	message: string;
	/** For `research`. */
	question: string;
	/** For `service`, as `domain.service`. */
	service: string;
	mode: Mode;
	/** `once`: a `<input type=date>` value. */
	date: string;
	/** `once`/`daily`/`weekly`: an `<input type=time>` value. */
	time: string;
	days: string[];
	minutes: string;
}

export type ScheduleResult =
	| { ok: true; payload: Record<string, unknown> }
	| { ok: false; error: string; field: keyof ScheduleForm };

export function blankScheduleForm(): ScheduleForm {
	return {
		title: '',
		kind: 'notify',
		message: '',
		question: '',
		service: '',
		mode: 'once',
		date: '',
		time: '',
		days: [],
		minutes: '30'
	};
}

/**
 * A naive local timestamp, which is what jarvis-core wants.
 *
 * NOT `toISOString()`. That converts to UTC using the BROWSER's offset, and the
 * browser may be a laptop in another country from the house. jarvis-core reads
 * a zone-less timestamp in the house's own zone, so sending the wall-clock
 * moment unchanged is the only reading that survives the console being
 * somewhere else.
 */
export function localTimestamp(date: string, time: string): string {
	return `${date}T${time.length === 5 ? `${time}:00` : time}`;
}

export function parseScheduleForm(form: ScheduleForm): ScheduleResult {
	const payload: Record<string, unknown> = { kind: form.kind };
	const title = form.title.trim();
	if (title) payload.title = title;

	if (form.kind === 'notify') {
		if (!form.message.trim()) {
			return { ok: false, error: 'What should it say?', field: 'message' };
		}
		payload.message = form.message.trim();
	} else if (form.kind === 'research') {
		if (!form.question.trim()) {
			return { ok: false, error: 'What should it find out?', field: 'question' };
		}
		payload.question = form.question.trim();
	} else {
		const service = form.service.trim();
		if (!/^[a-z0-9_]+\.[a-z0-9_]+$/i.test(service)) {
			return {
				ok: false,
				error: 'A service looks like `light.turn_on`.',
				field: 'service'
			};
		}
		payload.service = service;
	}

	if (form.mode === 'every') {
		const minutes = Number(form.minutes);
		if (!Number.isInteger(minutes) || minutes < MIN_EVERY_MINUTES) {
			return {
				ok: false,
				error: `The shortest repeat is every ${MIN_EVERY_MINUTES} minutes.`,
				field: 'minutes'
			};
		}
		payload.when = { mode: 'every', minutes };
		return { ok: true, payload };
	}

	if (!form.time) {
		return { ok: false, error: 'At what time?', field: 'time' };
	}

	if (form.mode === 'once') {
		if (!form.date) {
			return { ok: false, error: 'On what date?', field: 'date' };
		}
		payload.when = { mode: 'once', at: localTimestamp(form.date, form.time) };
		return { ok: true, payload };
	}

	if (form.mode === 'weekly' && !form.days.length) {
		return { ok: false, error: 'Which days?', field: 'days' };
	}
	payload.when = {
		mode: form.mode,
		at: form.time.slice(0, 5),
		days: form.mode === 'weekly' ? form.days : []
	};
	return { ok: true, payload };
}

/**
 * "in 4m" / "in 1h 30m" / "in 3 days" — the useful half of a timestamp.
 *
 * Hours carry their minutes rather than rounding. A job due in ninety minutes
 * rounds to "in 2h", which is half an hour of lie at exactly the range where
 * somebody is deciding whether to wait for it.
 */
export function until(epochSeconds: number | null, now: number = Date.now() / 1000): string {
	if (!epochSeconds) return '';
	const delta = epochSeconds - now;
	if (delta <= 0) return 'due';
	if (delta < 90) return 'in under a minute';
	if (delta < 3600) return `in ${Math.round(delta / 60)}m`;
	if (delta < 86400) {
		const hours = Math.floor(delta / 3600);
		const minutes = Math.round((delta % 3600) / 60);
		return minutes ? `in ${hours}h ${minutes}m` : `in ${hours}h`;
	}
	return `in ${Math.round(delta / 86400)} days`;
}

/**
 * One line under a job: when it next runs, or why it will not.
 *
 * A disabled job and a spent one-shot both have no next firing and are
 * completely different situations, so they do not get the same sentence.
 */
export function describeJob(job: ScheduledJob, now: number = Date.now() / 1000): string {
	if (!job.enabled) return 'off';
	if (!job.next_at) return job.when.mode === 'once' ? 'done' : 'not scheduled';
	return `${job.describes} · next ${until(job.next_at, now)}`;
}

/**
 * What went wrong last time, if anything did — and how much was missed.
 *
 * The miss count is surfaced deliberately: jarvis-core refuses to replay a
 * backlog, which is right, and a run that quietly did not happen is exactly the
 * thing somebody needs told rather than left to notice.
 */
export function jobFootnote(job: ScheduledJob): string {
	const parts: string[] = [];
	if (job.missed) {
		parts.push(`${job.missed} firing${job.missed === 1 ? '' : 's'} missed`);
	}
	if (job.last_result) parts.push(job.last_result);
	return parts.join(' · ');
}

export function readOnlyNote(job: ScheduledJob): string {
	return job.editable ? '' : 'defined in configuration.yaml — edit it there';
}

/** Soonest first, with the unscheduled ones last rather than first. */
export function sortJobs(jobs: ScheduledJob[]): ScheduledJob[] {
	return jobs
		.slice()
		.sort(
			(a, b) =>
				(a.next_at ?? Number.POSITIVE_INFINITY) - (b.next_at ?? Number.POSITIVE_INFINITY) ||
				a.created - b.created
		);
}
