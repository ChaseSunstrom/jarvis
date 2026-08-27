import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { buildGraph, layout, touchedBy } from './graph';

const notes = [
	{ id: 'boiler', title: 'Boiler serviced', tags: ['house', 'maintenance'], links: ['heating'], backlinks: [] },
	{ id: 'heating', title: 'Heating', tags: ['house'], links: [], backlinks: ['boiler'] },
	{ id: 'heat-pumps', title: 'Research — heat pumps', tags: ['research'], links: [], backlinks: [] }
];
const memory = [
	{ id: 'm1', text: 'I take my coffee black', tags: ['preference'] },
	{ id: 'm2', text: 'The spare key is in the blue tin by the boiler', tags: ['house'] }
];

describe('the knowledge graph', () => {
	it('makes a node of every note and every memory entry, prefixed by kind', () => {
		const { nodes } = buildGraph(notes, memory);
		expect(nodes.map((n) => n.id)).toEqual([
			'note:boiler',
			'note:heating',
			'note:heat-pumps',
			'memory:m1',
			'memory:m2'
		]);
		expect(nodes.find((n) => n.id === 'memory:m2')!.label.length).toBeLessThanOrEqual(42);
	});

	it('draws a link edge for a wiki link and a tag edge for a shared tag, each once', () => {
		const { edges } = buildGraph(notes, memory);
		const link = edges.filter((e) => e.kind === 'link');
		expect(link).toEqual([{ from: 'note:boiler', to: 'note:heating', kind: 'link' }]);
		// house: boiler, heating, m2 → three pairs, minus the pair that is already a link.
		const tag = edges.filter((e) => e.kind === 'tag');
		expect(tag.length).toBe(2);
		expect(new Set(edges.map((e) => [e.from, e.to].sort().join('|'))).size).toBe(edges.length);
	});

	it('does not draw a hairball for a tag on everything', () => {
		const many = Array.from({ length: 12 }, (_, i) => ({ id: `n${i}`, title: `Note ${i}`, tags: ['all'] }));
		expect(buildGraph(many, []).edges).toEqual([]);
	});

	it('lays the same graph out the same way twice, inside the box', () => {
		const { nodes, edges } = buildGraph(notes, memory);
		const a = layout(nodes, edges, { width: 600, height: 400 });
		const b = layout(nodes, edges, { width: 600, height: 400 });
		expect(a.nodes).toEqual(b.nodes);
		for (const node of a.nodes) {
			expect(Number.isFinite(node.x) && Number.isFinite(node.y)).toBe(true);
			expect(node.x).toBeGreaterThanOrEqual(0);
			expect(node.x).toBeLessThanOrEqual(600);
			expect(node.y).toBeGreaterThanOrEqual(0);
			expect(node.y).toBeLessThanOrEqual(400);
		}
	});

	it('pulls linked nodes closer than unlinked ones', () => {
		const { nodes, edges } = buildGraph(notes, memory);
		const placed = layout(nodes, edges, { width: 600, height: 400 });
		const at = (id: string) => placed.nodes.find((n) => n.id === id)!;
		const dist = (p: string, q: string) => Math.hypot(at(p).x - at(q).x, at(p).y - at(q).y);
		expect(dist('note:boiler', 'note:heating')).toBeLessThan(dist('note:heat-pumps', 'memory:m1'));
	});

	it('keeps two nodes that start on one spot apart', () => {
		const same = [
			{ id: 'a', title: 'A' },
			{ id: 'b', title: 'B' }
		];
		const placed = layout(buildGraph(same, []).nodes, [], { width: 300, height: 300, iterations: 50 });
		expect(Math.hypot(placed.nodes[0].x - placed.nodes[1].x, placed.nodes[0].y - placed.nodes[1].y)).toBeGreaterThan(10);
	});

	it('names what a note tool touched', () => {
		const { nodes } = buildGraph(notes, memory);
		expect(touchedBy('note_append', { note_id: 'heating', text: 'x' }, nodes)).toEqual(['note:heating']);
		expect(touchedBy('note_search', { query: 'heat pumps' }, nodes)).toEqual(['note:heating', 'note:heat-pumps']);
		expect(touchedBy('note_search', { query: 'zzz' }, nodes)).toEqual([]);
		expect(touchedBy('turn_on', { name: 'lamp' }, nodes)).toEqual([]);
	});

	it('handles nothing', () => {
		expect(layout([], [], {}).nodes).toEqual([]);
	});
});

describe('the contract the phone mirrors', () => {
	const contract = JSON.parse(readFileSync(new URL('../../../../tests/contracts/knowledge_graph.json', import.meta.url), 'utf8'));
	it('builds exactly the nodes and edges the contract pins', () => {
		const { nodes, edges } = buildGraph(contract.notes, contract.memory);
		expect(nodes.map((n) => ({ id: n.id, label: n.label, kind: n.kind }))).toEqual(contract.nodes);
		expect(edges).toEqual(contract.edges);
	});
});
