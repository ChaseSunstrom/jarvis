// The knowledge graph's layout, as pure functions.
//
// Notes and memory entries are nodes; `[[links]]` and shared tags are edges.
// The layout is a small force simulation — springs along edges, repulsion
// between every pair, a weak pull to the centre — run for a fixed number of
// steps from SEEDED starting positions, so the same graph always lands the
// same way and a test can assert on it. Nothing here touches the DOM;
// `Graph.svelte` draws the answer.

export type NodeKind = 'note' | 'memory';

export interface GraphNode {
	id: string;
	label: string;
	kind: NodeKind;
	tags?: string[];
}

export interface GraphEdge {
	from: string;
	to: string;
	/** `link` is a wiki link; `tag` is a shared tag, drawn quieter. */
	kind: 'link' | 'tag';
}

export interface Placed extends GraphNode {
	x: number;
	y: number;
}

export interface Layout {
	nodes: Placed[];
	edges: GraphEdge[];
	width: number;
	height: number;
}

/** A note as the API lists it, and a memory entry likewise — only what the graph needs. */
export interface NoteLike {
	id: string;
	title: string;
	tags?: string[];
	links?: string[];
	backlinks?: string[];
}
export interface MemoryLike {
	id: string;
	text: string;
	tags?: string[];
}

/** Nodes and edges from what the two lists say. */
export function buildGraph(notes: NoteLike[], memory: MemoryLike[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
	const nodes: GraphNode[] = [
		...notes.map((n) => ({ id: `note:${n.id}`, label: n.title, kind: 'note' as const, tags: n.tags ?? [] })),
		...memory.map((m) => ({
			id: `memory:${m.id}`,
			label: m.text.length > 42 ? `${m.text.slice(0, 41)}…` : m.text,
			kind: 'memory' as const,
			tags: m.tags ?? []
		}))
	];
	const known = new Set(nodes.map((n) => n.id));
	const byTitle = new Map(notes.map((n) => [n.title.toLowerCase(), `note:${n.id}`]));
	const seen = new Set<string>();
	const edges: GraphEdge[] = [];
	const add = (from: string, to: string, kind: GraphEdge['kind']) => {
		if (from === to || !known.has(from) || !known.has(to)) return;
		const key = [from, to].sort().join('|');
		if (seen.has(key)) return;
		seen.add(key);
		edges.push({ from, to, kind });
	};
	for (const note of notes) {
		for (const target of [...(note.links ?? []), ...(note.backlinks ?? [])]) {
			// A link names a slug or a title; both are tried.
			const to = known.has(`note:${target}`) ? `note:${target}` : byTitle.get(String(target).toLowerCase());
			if (to) add(`note:${note.id}`, to, 'link');
		}
	}
	// Shared tags: every pair that shares one. Bounded, because a tag on
	// everything ("house") would otherwise draw a hairball — a tag shared by
	// more than TAG_FANOUT nodes says nothing about any two of them.
	const TAG_FANOUT = 8;
	const byTag = new Map<string, string[]>();
	for (const node of nodes) for (const tag of node.tags ?? []) byTag.set(tag, [...(byTag.get(tag) ?? []), node.id]);
	for (const ids of byTag.values()) {
		if (ids.length > TAG_FANOUT) continue;
		for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) add(ids[i], ids[j], 'tag');
	}
	return { nodes, edges };
}

/** A small deterministic PRNG, so a layout is a function of its input. */
function seeded(text: string): () => number {
	let h = 2166136261;
	for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
	return () => {
		h = Math.imul(h ^ (h >>> 15), 2246822507);
		h = Math.imul(h ^ (h >>> 13), 3266489909);
		h ^= h >>> 16;
		return (h >>> 0) / 4294967296;
	};
}

export interface LayoutOptions {
	width?: number;
	height?: number;
	iterations?: number;
}

/**
 * Place the nodes.
 *
 * Fruchterman–Reingold in miniature: repulsion ∝ k²/d, attraction along an
 * edge ∝ d²/k, a gentle pull to the centre, and a temperature that cools so
 * the picture settles rather than jitters. Fixed iterations, seeded start, no
 * randomness in the loop — the result is reproducible.
 */
export function layout(nodes: GraphNode[], edges: GraphEdge[], options: LayoutOptions = {}): Layout {
	const width = options.width ?? 600;
	const height = options.height ?? 400;
	const iterations = options.iterations ?? 220;
	const n = nodes.length;
	if (n === 0) return { nodes: [], edges: [], width, height };
	const rand = seeded(nodes.map((node) => node.id).join('\n'));
	const pad = Math.min(width, height) * 0.12;
	const xs = nodes.map(() => pad + rand() * (width - 2 * pad));
	const ys = nodes.map(() => pad + rand() * (height - 2 * pad));
	const index = new Map(nodes.map((node, i) => [node.id, i]));
	const links = edges
		.map((edge) => [index.get(edge.from), index.get(edge.to), edge.kind === 'link' ? 1 : 0.45] as const)
		.filter(([a, b]) => a !== undefined && b !== undefined) as [number, number, number][];
	const area = width * height;
	const k = Math.sqrt(area / n) * 0.7;
	let temperature = Math.min(width, height) / 8;
	const cool = temperature / iterations;
	for (let step = 0; step < iterations; step++) {
		const dx = new Array<number>(n).fill(0);
		const dy = new Array<number>(n).fill(0);
		for (let i = 0; i < n; i++) {
			for (let j = i + 1; j < n; j++) {
				let ddx = xs[i] - xs[j];
				let ddy = ys[i] - ys[j];
				let d = Math.hypot(ddx, ddy);
				if (d < 0.01) {
					// Two nodes on one spot have no direction to part in; give them one.
					ddx = (i - j) * 0.01;
					ddy = 0.01;
					d = Math.hypot(ddx, ddy);
				}
				const force = (k * k) / d;
				dx[i] += (ddx / d) * force;
				dy[i] += (ddy / d) * force;
				dx[j] -= (ddx / d) * force;
				dy[j] -= (ddy / d) * force;
			}
		}
		for (const [a, b, weight] of links) {
			const ddx = xs[a] - xs[b];
			const ddy = ys[a] - ys[b];
			const d = Math.hypot(ddx, ddy) || 0.01;
			const force = ((d * d) / k) * weight;
			dx[a] -= (ddx / d) * force;
			dy[a] -= (ddy / d) * force;
			dx[b] += (ddx / d) * force;
			dy[b] += (ddy / d) * force;
		}
		for (let i = 0; i < n; i++) {
			// The pull to the centre, so an island does not drift to a corner.
			dx[i] += (width / 2 - xs[i]) * 0.02;
			dy[i] += (height / 2 - ys[i]) * 0.02;
			const d = Math.hypot(dx[i], dy[i]) || 0.01;
			const capped = Math.min(d, temperature);
			xs[i] = Math.min(width - pad, Math.max(pad, xs[i] + (dx[i] / d) * capped));
			ys[i] = Math.min(height - pad, Math.max(pad, ys[i] + (dy[i] / d) * capped));
		}
		temperature = Math.max(0.5, temperature - cool);
	}
	return {
		nodes: nodes.map((node, i) => ({ ...node, x: Math.round(xs[i] * 10) / 10, y: Math.round(ys[i] * 10) / 10 })),
		edges: edges.filter((edge) => index.has(edge.from) && index.has(edge.to)),
		width,
		height
	};
}

/**
 * Which nodes a tool call touched, from its name and arguments.
 *
 * `note_append`/`note_search` name a note id; `note_search` with only a query
 * touches every note whose title shares a word with it; `remember`/`recall`
 * touch nothing until the memory ids arrive. Returns graph node ids.
 */
export function touchedBy(
	name: string,
	args: Record<string, unknown> | undefined,
	nodes: GraphNode[]
): string[] {
	const a = args ?? {};
	if (!name.startsWith('note_')) return [];
	const noteId = typeof a.note_id === 'string' ? a.note_id : typeof a.id === 'string' ? a.id : '';
	if (noteId && nodes.some((node) => node.id === `note:${noteId}`)) return [`note:${noteId}`];
	const query = typeof a.query === 'string' ? a.query : typeof a.title === 'string' ? a.title : '';
	if (!query) return [];
	const words = query
		.toLowerCase()
		.split(/[^a-z0-9]+/)
		.filter((word) => word.length > 2);
	return nodes
		.filter((node) => node.kind === 'note' && words.some((word) => node.label.toLowerCase().includes(word)))
		.map((node) => node.id);
}
