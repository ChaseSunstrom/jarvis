import { describe, expect, it } from 'vitest';
import {
	describeActive,
	describeConnections,
	describeInstance,
	isReady,
	newestFirst,
	runTone,
	type N8nExecution,
	type N8nGraph,
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
