/**
 * What one turn did, and what each step of it cost.
 *
 * jarvis-core groups every tool call, model call, approval and subagent into a
 * trace keyed on the context id the bus already carried. This is the reading
 * half: the shapes, and the two derivations a person actually wants — how long
 * went on the model against the tools, and what the whole thing cost in tokens.
 *
 * Deliberately not a chart. A trace is a list of things that happened in order,
 * and the honest rendering of that is a list.
 */

export interface Span {
	kind: string;
	name: string;
	started: number;
	ms: number | null;
	ok: boolean;
	error: string | null;
	data: Record<string, unknown>;
}

export interface Trace {
	id: string;
	origin: string;
	label: string;
	task_id: string | null;
	started: number;
	ms: number | null;
	truncated: number;
	spans: number;
	tools: number;
	model_calls: number;
	prompt_tokens: number;
	completion_tokens: number;
	model_ms: number;
	tool_ms: number;
	errors: number;
	spans_detail?: Span[];
}

/** The spans, however the payload spelled the field. */
export function spansOf(trace: Trace | null | undefined): Span[] {
	if (!trace) return [];
	const raw = (trace as unknown as { spans?: unknown }).spans;
	return Array.isArray(raw) ? (raw as Span[]) : (trace.spans_detail ?? []);
}

/** `1.2 s` / `340 ms` / `—`. Milliseconds below a second, because that is the scale. */
export function duration(ms: number | null | undefined): string {
	if (ms === null || ms === undefined) return '—';
	if (ms < 1000) return `${Math.round(ms)} ms`;
	return `${(ms / 1000).toFixed(1)} s`;
}

/** `1,240` — thousands separated, because token counts are read, not computed. */
export function tokens(count: number | null | undefined): string {
	return (count ?? 0).toLocaleString('en-GB');
}

/**
 * Where the time went, as whole percents that add to 100.
 *
 * Model, tools, and everything else — which is the interesting bucket: a turn
 * that spent four seconds in neither was waiting on a person or on the network.
 */
export function timeSplit(trace: Trace | null | undefined): {
	model: number;
	tools: number;
	other: number;
} {
	const total = trace?.ms ?? 0;
	if (!trace || total <= 0) return { model: 0, tools: 0, other: 0 };
	const model = Math.min(100, Math.round(((trace.model_ms ?? 0) / total) * 100));
	const tools = Math.min(100 - model, Math.round(((trace.tool_ms ?? 0) / total) * 100));
	return { model, tools, other: Math.max(0, 100 - model - tools) };
}

/** One line a person can read: what it was, how long, what it cost. */
export function describeTrace(trace: Trace | null | undefined): string {
	if (!trace) return 'no trace was recorded for this task';
	const parts = [
		`${trace.spans} step${trace.spans === 1 ? '' : 's'}`,
		`${trace.model_calls} model call${trace.model_calls === 1 ? '' : 's'}`,
		`${tokens(trace.prompt_tokens + trace.completion_tokens)} tokens`
	];
	if (trace.errors) parts.push(`${trace.errors} failed`);
	if (trace.truncated) parts.push(`${trace.truncated} not recorded`);
	return parts.join(' · ');
}

/** The tone a span's outcome should be shown in. */
export function spanTone(span: Span): 'ok' | 'danger' | 'live' {
	if (!span.ok) return 'danger';
	return span.ms === null ? 'live' : 'ok';
}
