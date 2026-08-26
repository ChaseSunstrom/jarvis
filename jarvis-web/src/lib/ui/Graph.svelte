<!--
@component
The knowledge graph: notes and memory entries as nodes, `[[links]]` and shared
tags as edges, laid out by `$lib/knowledge/graph` and drawn in with the
stagger. A node lights and its edges pulse when something touches it — a
turn that read a memory entry, a tool that wrote a note — and settles again
over `--jv-dur-blink`. Click or press a node to select it; the parent decides
what selecting means (opening the note, scrolling to the entry).

```svelte
<Graph {nodes} {edges} selected={openId} pulses={recent} onselect={(id) => open(id)} />
```

Under reduced motion nothing draws in and nothing pulses; the lit state is
still shown, because it is information.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { layout, type GraphEdge, type GraphNode } from '$lib/knowledge/graph';
	import { staggerDelay } from '$lib/motion';
	import { tokenMs } from '$lib/tokens';

	export interface Pulse {
		id: string;
		/** `performance.now()` when it was touched. */
		at: number;
	}
	interface Props {
		nodes: GraphNode[];
		edges: GraphEdge[];
		selected?: string;
		/** Nodes touched recently; each lights for one blink. */
		pulses?: Pulse[];
		/** The drawing's height in its own units; the width is 600. */
		height?: number;
		onselect?: (id: string) => void;
		testid?: string;
	}
	let { nodes, edges, selected = '', pulses = [], height = 380, onselect, testid = 'graph' }: Props = $props();

	const WIDTH = 600;
	const placed = $derived(layout(nodes, edges, { width: WIDTH, height }));
	const at = $derived(new Map(placed.nodes.map((node) => [node.id, node])));

	// The lit window, from the tokens: a node stays lit for one slow blink.
	const LIT_MS = tokenMs('--jv-dur-blink');
	let now = $state(0);
	let ticker: ReturnType<typeof setInterval> | null = null;

	const lit = $derived(new Set(pulses.filter((p) => now - p.at < LIT_MS).map((p) => p.id)));

	// Tick only while something is lit, so an idle graph costs nothing.
	$effect(() => {
		const latest = pulses.reduce((m, p) => Math.max(m, p.at), 0);
		if (!latest) return;
		now = performance.now();
		if (ticker) clearInterval(ticker);
		ticker = setInterval(() => {
			now = performance.now();
			if (now - latest >= LIT_MS && ticker) {
				clearInterval(ticker);
				ticker = null;
			}
		}, 120);
		return () => {
			if (ticker) clearInterval(ticker);
			ticker = null;
		};
	});

	onMount(() => () => {
		if (ticker) clearInterval(ticker);
	});

	function choose(id: string): void {
		onselect?.(id);
	}
	function onKey(event: KeyboardEvent, id: string): void {
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			choose(id);
		}
	}
	/** Where the label goes: to the right, unless the node is near the right edge. */
	const labelSide = (x: number) => (x > WIDTH * 0.72 ? 'end' : 'start');
</script>

{#if placed.nodes.length}
	<svg
		class="graph"
		viewBox="0 0 {WIDTH} {height}"
		role="group"
		aria-label="Knowledge graph: {nodes.length} nodes, {edges.length} links"
		data-testid={testid}
		data-nodes={nodes.length}
		data-lit={lit.size}
	>
		<g class="edges">
			{#each placed.edges as edge, i (edge.from + edge.to)}
				{@const a = at.get(edge.from)}
				{@const b = at.get(edge.to)}
				{#if a && b}
					<line
						class="edge {edge.kind}"
						class:lit={lit.has(edge.from) || lit.has(edge.to)}
						class:near={selected === edge.from || selected === edge.to}
						x1={a.x}
						y1={a.y}
						x2={b.x}
						y2={b.y}
						style:animation-delay="{staggerDelay(i)}ms"
					/>
				{/if}
			{/each}
		</g>
		<g class="nodes">
			{#each placed.nodes as node, i (node.id)}
				<g
					class="node {node.kind}"
					class:selected={selected === node.id}
					class:lit={lit.has(node.id)}
					role="button"
					tabindex="0"
					aria-label={node.label}
					aria-pressed={selected === node.id}
					data-testid="graph-node-{node.id}"
					data-kind={node.kind}
					transform="translate({node.x} {node.y})"
					style:animation-delay="{staggerDelay(i)}ms"
					onclick={() => choose(node.id)}
					onkeydown={(event) => onKey(event, node.id)}
				>
					<circle class="halo" r="14" />
					<circle class="hit" r="12" />
					{#if node.kind === 'note'}
						<circle class="body" r="6" />
						<circle class="core" r="1.6" />
					{:else}
						<circle class="body" r="4" />
					{/if}
					<text
						x={labelSide(node.x) === 'start' ? 11 : -11}
						y="4"
						text-anchor={labelSide(node.x)}
					>{node.label}</text>
				</g>
			{/each}
		</g>
	</svg>
{:else}
	<p class="none" data-testid="{testid}-empty">Nothing to draw yet: a note or a remembered fact becomes a point here.</p>
{/if}

<style>
	.graph {
		display: block;
		width: 100%;
		height: auto;
		overflow: visible;
	}
	.edge {
		stroke: var(--jv-line);
		stroke-width: 1;
		/* Drawn in: the dash is the line's own length, offset away and pulled back. */
		stroke-dasharray: 1000;
		stroke-dashoffset: 1000;
		animation: draw var(--jv-dur-sweep) var(--jv-ease-out) forwards;
		transition: stroke var(--jv-dur-base) var(--jv-ease-out);
	}
	.edge.tag {
		stroke: var(--jv-line-hair);
		stroke-dasharray: 1000;
	}
	.edge.near {
		stroke: var(--jv-text-dim);
	}
	.edge.lit {
		stroke: var(--jv-accent);
		animation: draw var(--jv-dur-sweep) var(--jv-ease-out) forwards, jv-blink var(--jv-dur-pulse) var(--jv-ease-in-out) infinite;
	}
	.node {
		cursor: pointer;
		outline: none;
		animation: arrive var(--jv-dur-enter) var(--jv-ease-out) both;
	}
	.hit {
		fill: transparent;
	}
	.body {
		fill: var(--jv-panel);
		stroke: var(--jv-text-dim);
		stroke-width: 1;
		transition: stroke var(--jv-dur-base) var(--jv-ease-out), fill var(--jv-dur-base) var(--jv-ease-out);
	}
	.memory .body {
		fill: var(--jv-text-faint);
		stroke: none;
	}
	.core {
		fill: var(--jv-text-dim);
	}
	.halo {
		fill: var(--jv-accent);
		opacity: 0;
		transform-origin: 0 0;
		transition: opacity var(--jv-dur-slow) var(--jv-ease-out);
	}
	text {
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-xs);
		fill: var(--jv-text-dim);
		paint-order: stroke;
		stroke: var(--jv-bg);
		stroke-width: 3;
		transition: fill var(--jv-dur-fast) var(--jv-ease-out);
		pointer-events: none;
	}
	.node:hover .body,
	.node:focus-visible .body {
		stroke: var(--jv-text);
	}
	.node:hover text,
	.node:focus-visible text {
		fill: var(--jv-text);
	}
	.node:focus-visible .halo {
		opacity: 0.18;
	}
	.node.selected .body {
		stroke: var(--jv-accent);
		fill: var(--jv-wash-strong);
	}
	.node.selected.memory .body {
		fill: var(--jv-accent);
	}
	.node.selected text {
		fill: var(--jv-text-bright);
	}
	/* Touched: the halo comes up and breathes, then the blink window closes. */
	.node.lit .halo {
		opacity: 0.35;
		animation: pulse var(--jv-dur-pulse) var(--jv-ease-in-out) infinite alternate;
	}
	.node.lit .body {
		stroke: var(--jv-accent);
	}
	.node.lit.memory .body {
		fill: var(--jv-accent);
	}
	.node.lit text {
		fill: var(--jv-text-bright);
	}
	.none {
		margin: 0;
		padding: var(--jv-space-5) var(--jv-space-4);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-faint);
		text-align: center;
	}
	@keyframes draw {
		to {
			stroke-dashoffset: 0;
		}
	}
	@keyframes arrive {
		from {
			opacity: 0;
		}
		to {
			opacity: 1;
		}
	}
	@keyframes pulse {
		from {
			transform: scale(1);
			opacity: 0.35;
		}
		to {
			transform: scale(1.5);
			opacity: 0.12;
		}
	}
</style>
