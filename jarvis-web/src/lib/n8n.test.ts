import { describe, expect, it } from 'vitest';
import {
	canUseBuilder,
	capabilityLines,
	describeActive,
	describeConnections,
	describeInstance,
	describeSpeaker,
	healthTone,
	isReady,
	newestFirst,
	runTone,
	whyNoBuilder,
	type N8nCapabilities,
	type N8nCheck,
	type N8nExecution,
	type N8nGraph,
	type N8nHealth,
	type N8nInstance
} from './n8n';

const instance = (over: Partial<N8nInstance> = {}): N8nInstance => ({
	url: 'http://n8n.lan:5678',
	has_key: true,
	allow_activate: false,
	tag: 'jarvis',
	configured: true,
	...over
});

describe('describeInstance', () => {
	/**
	 * Three states, three different next steps — which is why this is not a
	 * boolean. "Configured" is not "working", and the difference is a support
	 * ticket.
	 */
	it('tells an unconfigured server which key to add', () => {
		const said = describeInstance(instance({ url: '', configured: false }));
		expect(said).toContain('n8n: url:');
		expect(said).toContain('configuration.yaml');
	});

	it('tells a keyless server where to make one', () => {
		const said = describeInstance(instance({ has_key: false }));
		expect(said).toContain('Settings → n8n API');
		expect(said).toContain('N8N_API_KEY');
	});

	it('does not claim a configured instance works', () => {
		const said = describeInstance(instance());
		expect(said).toContain('CHECK');
		expect(said).not.toMatch(/connected|working\b/i);
	});

	it('survives having no instance at all', () => {
		expect(describeInstance(null)).toContain('configuration.yaml');
	});
});

describe('describeConnections', () => {
	/**
	 * The console half of "ask for connections". Jarvis strips the credential
	 * id the model guessed, so this sentence is the only thing standing
	 * between "created" and "why is it broken".
	 */
	it('names the credential AND the node, because a name alone is not actionable', () => {
		const said = describeConnections([{ node: 'Gmail', credential_type: 'gmailOAuth2' }]);
		expect(said).toContain('gmailOAuth2');
		expect(said).toContain('Gmail');
	});

	it('says where to do it and what to do after', () => {
		const said = describeConnections([{ node: 'Gmail', credential_type: 'gmailOAuth2' }]);
		expect(said).toContain('Credentials');
		expect(said).toContain('activate');
	});

	it('reads as a sentence with several', () => {
		const said = describeConnections([
			{ node: 'Gmail', credential_type: 'gmailOAuth2' },
			{ node: 'Sheets', credential_type: 'googleApi' },
			{ node: 'Slack', credential_type: 'slackApi' }
		]);
		expect(said).toContain('and slackApi for “Slack”');
		expect(said).not.toContain(', and ,');
	});

	it('says nothing when there is nothing to connect', () => {
		expect(describeConnections([])).toBe('');
	});
});

describe('isReady', () => {
	const graph = (needed: N8nGraph['connections_needed']): N8nGraph => ({
		id: '1',
		name: 'x',
		active: false,
		nodes: [],
		edges: [],
		connections_needed: needed
	});

	it('is false while anything is unconnected', () => {
		expect(isReady(graph([{ node: 'Gmail', credential_type: 'gmailOAuth2' }]))).toBe(false);
	});

	it('is true when nothing is', () => {
		expect(isReady(graph([]))).toBe(true);
	});

	it('is false with no graph, rather than throwing', () => {
		expect(isReady(null)).toBe(false);
	});
});

describe('describeActive', () => {
	it('says what active MEANS, because a bare flag does not say which way is safe', () => {
		const live = describeActive({ id: '1', name: 'x', active: true, nodes: 1, tags: [], updated_at: '' });
		expect(live).toContain('firing');
		expect(describeActive({ id: '1', name: 'x', active: false, nodes: 1, tags: [], updated_at: '' })).toBe(
			'off'
		);
	});
});

describe('runTone', () => {
	it("uses n8n's own vocabulary", () => {
		expect(runTone('success')).toBe('good');
		expect(runTone('error')).toBe('bad');
		expect(runTone('crashed')).toBe('bad');
		expect(runTone('running')).toBe('busy');
		expect(runTone('waiting')).toBe('busy');
	});

	it('does not colour something it does not recognise', () => {
		// `canceled` is n8n's spelling, and it is neither good nor bad.
		expect(runTone('canceled')).toBe('idle');
		expect(runTone('')).toBe('idle');
	});
});

describe('newestFirst', () => {
	const run = (id: string, started: string): N8nExecution => ({
		id,
		workflow_id: 'w',
		status: 'success',
		started_at: started,
		stopped_at: '',
		mode: 'trigger'
	});

	it('is the order anybody wants runs in', () => {
		const sorted = newestFirst([
			run('old', '2026-01-01T00:00:00Z'),
			run('new', '2026-02-01T00:00:00Z')
		]);
		expect(sorted.map((r) => r.id)).toEqual(['new', 'old']);
	});

	it('does not mutate what it was given', () => {
		const input = [run('a', '2026-01-01T00:00:00Z'), run('b', '2026-02-01T00:00:00Z')];
		newestFirst(input);
		expect(input.map((r) => r.id)).toEqual(['a', 'b']);
	});
});

// ---------------------------------------------------------------------------
// the three-layer CHECK
// ---------------------------------------------------------------------------
const caps = (over: Partial<N8nCapabilities> = {}): N8nCapabilities => ({
	api: { available: true, reason: '', detail: 'Connected to http://n8n.lan:5678.' },
	login: { available: true, reason: '', detail: 'Logged in as jarvis@example.com.' },
	builder: {
		available: false,
		reason: 'licence',
		detail:
			"A model is wired up to n8n's AI builder, but the instance's licence does not " +
			'include the feature — those are two separate switches and only the first one is yours.'
	},
	checked_at: 1,
	...over
});

const check = (over: Partial<N8nCheck> = {}): N8nCheck => ({
	ok: true,
	detail: 'Connected.',
	capabilities: caps(),
	...over
});

describe('capabilityLines', () => {
	it('reports the three layers separately, because they fail separately', () => {
		// The failure this replaces: one "n8n: no", which sends somebody to
		// check an API key that was right all along.
		const lines = capabilityLines(check());
		expect(lines.map((l) => l.label)).toEqual(['Public API', 'Login', 'AI builder']);
		expect(lines.map((l) => l.available)).toEqual([true, true, false]);
	});

	it('marks only the public API as blocking', () => {
		// Everything else depends on it. A missing login costs you three
		// features; a missing key costs you the page.
		const lines = capabilityLines(check({ capabilities: caps({ api: { available: false, reason: 'unreachable', detail: '401' } }) }));
		expect(lines[0].blocking).toBe(true);
		expect(lines.slice(1).every((l) => !l.blocking)).toBe(true);
	});

	it('falls back to one line against a backend that predates the probe', () => {
		// Not an error — an older jarvis-core. Inventing two empty lines would
		// report an absence that has not been measured.
		const lines = capabilityLines({ ok: false, detail: 'n8n refused the API key (401).' });
		expect(lines).toHaveLength(1);
		expect(lines[0].detail).toContain('401');
		expect(lines[0].blocking).toBe(true);
	});

	it('is empty before anything has been checked', () => {
		expect(capabilityLines(null)).toEqual([]);
	});
});

describe('canUseBuilder', () => {
	it('is false until a check has actually said yes', () => {
		// A form that submits into a 403 is worse than no form.
		expect(canUseBuilder(null)).toBe(false);
		expect(canUseBuilder({ ok: true, detail: 'fine' })).toBe(false);
		expect(canUseBuilder(check())).toBe(false);
	});

	it('is true when the licence includes it', () => {
		expect(
			canUseBuilder(
				check({ capabilities: caps({ builder: { available: true, reason: '', detail: 'yes' } }) })
			)
		).toBe(true);
	});
});

describe('whyNoBuilder', () => {
	it('gives the sentence that names which switch is missing', () => {
		// The state most self-hosted users are in: they wired a model up and
		// assume that was the feature.
		expect(whyNoBuilder(check())).toContain('two separate switches');
	});

	it('says nothing when it works, and nothing before a check', () => {
		expect(
			whyNoBuilder(
				check({ capabilities: caps({ builder: { available: true, reason: '', detail: 'yes' } }) })
			)
		).toBe('');
		// An absence before CHECK is not a finding.
		expect(whyNoBuilder(null)).toBe('');
	});
});

describe('healthTone', () => {
	const health = (over: Partial<N8nHealth> = {}): N8nHealth => ({
		workflow_id: 'wf-1',
		name: 'x',
		active: true,
		healthy: true,
		summary: '',
		connections_needed: [],
		runs: 2,
		failures: 0,
		running_now: 0,
		last_status: 'success',
		last_run: '',
		next_step: '',
		...over
	});

	it('separates failing from merely unfinished', () => {
		// "Two runs failed" and "connected but never run" are both unhealthy
		// and want different colours: one is broken, the other is waiting.
		expect(healthTone(health({ healthy: false, failures: 2 }))).toBe('bad');
		expect(healthTone(health({ healthy: false, failures: 0, runs: 0 }))).toBe('idle');
	});

	it('shows a live run as busy rather than merely good', () => {
		expect(healthTone(health())).toBe('good');
		expect(healthTone(health({ running_now: 1 }))).toBe('busy');
	});

	it('is idle before anything has been read', () => {
		expect(healthTone(null)).toBe('idle');
	});
});

describe('describeSpeaker', () => {
	it('names the builder as somebody else', () => {
		// Those words were composed by a different AI on somebody else's
		// machine. Left unlabelled they read as Jarvis's own.
		expect(describeSpeaker('builder')).toContain("n8n's");
		expect(describeSpeaker('you')).toBe('you');
		expect(describeSpeaker('tool')).toBe('it ran');
	});

	it('shows an unknown role rather than swallowing the line', () => {
		expect(describeSpeaker('something-new')).toBe('something-new');
		expect(describeSpeaker('')).toBe('—');
	});
});
