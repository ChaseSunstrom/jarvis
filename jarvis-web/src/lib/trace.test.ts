import { describe, expect, it } from 'vitest';
import { describeTrace, duration, spanTone, timeSplit, tokens, type Span, type Trace } from './trace';

const trace = (over: Partial<Trace> = {}): Trace => ({
	id: 't', origin: 'llm', label: 'turn', task_id: null, started: 0, ms: 10_000,
	truncated: 0, spans: 5, tools: 2, model_calls: 3,
	prompt_tokens: 4000, completion_tokens: 120, model_ms: 6000, tool_ms: 1000, errors: 0,
	...over
});

describe('duration', () => {
	it('reads in the scale it is at', () => {
		expect(duration(340)).toBe('340 ms');
		expect(duration(1200)).toBe('1.2 s');
	});
	it('says nothing rather than zero when a span is still open', () => {
		expect(duration(null)).toBe('—');
		expect(duration(undefined)).toBe('—');
	});
});

describe('timeSplit', () => {
	it('accounts for the time that went nowhere', () => {
		// Six seconds of model and one of tools, out of ten: the other three
		// are the interesting number — waiting on a person, or on the network.
		expect(timeSplit(trace())).toEqual({ model: 60, tools: 10, other: 30 });
	});
	it('never sums past a hundred, whatever the clocks say', () => {
		const split = timeSplit(trace({ ms: 1000, model_ms: 900, tool_ms: 800 }));
		expect(split.model + split.tools + split.other).toBe(100);
	});
	it('is all zero for a trace with no duration yet', () => {
		expect(timeSplit(trace({ ms: null }))).toEqual({ model: 0, tools: 0, other: 0 });
		expect(timeSplit(null)).toEqual({ model: 0, tools: 0, other: 0 });
	});
});

describe('describeTrace', () => {
	it('is one line: what it did and what it cost', () => {
		expect(describeTrace(trace())).toBe('5 steps · 3 model calls · 4,120 tokens');
	});
	it('says when something failed or was dropped', () => {
		expect(describeTrace(trace({ errors: 1, truncated: 12 }))).toContain('1 failed');
		expect(describeTrace(trace({ errors: 1, truncated: 12 }))).toContain('12 not recorded');
	});
	it('says so plainly when tracing is off', () => {
		expect(describeTrace(null)).toContain('no trace');
	});
});

describe('spanTone', () => {
	const span = (over: Partial<Span> = {}): Span => ({
		kind: 'tool', name: 'get_state', started: 0, ms: 12, ok: true, error: null, data: {}, ...over
	});
	it('separates running from finished from failed', () => {
		expect(spanTone(span())).toBe('ok');
		expect(spanTone(span({ ms: null }))).toBe('live');
		expect(spanTone(span({ ok: false, error: 'refused' }))).toBe('danger');
	});
});

describe('tokens', () => {
	it('groups thousands, because these are read not computed', () => {
		expect(tokens(12345)).toBe('12,345');
		expect(tokens(null)).toBe('0');
	});
});
