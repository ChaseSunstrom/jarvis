<script lang="ts">
	/**
	 * KNOWLEDGE: what Jarvis has written down and what it remembers, as one graph.
	 *
	 * Notes and memory entries are the nodes; `[[links]]` and shared tags the
	 * edges. The graph is the hero — it is the one picture that answers "what
	 * does Jarvis know" — and it is live: a turn that read a remembered fact
	 * lights that fact (`voice_pipeline_event` `intent-end` carries the ids the
	 * model was given), a tool that wrote or searched a note lights the note,
	 * and the picture settles again. Selecting a node is a navigation, so a
	 * link to a note is a link to its point on the map.
	 *
	 * The sections beside it (Notes, Memory) keep their own connections and
	 * their own four states; this layout owns only the graph's.
	 */
	import { onDestroy, onMount, type Snippet } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { openConnection, describeError, type Connection, type ConnectionStatus } from '$lib/connection';
	import { isUnsupported } from '$lib/jarvisClient';
	import type { BusEvent, Subscription } from '$lib/jarvisClient';
	import { sectionsOf } from '$lib/screens';
	import { buildGraph, touchedBy, type MemoryLike, type NoteLike } from '$lib/knowledge/graph';
	import { knowledge } from '$lib/knowledge/store.svelte';
	import { Graph, Panel, ScreenTitle, SectionStrip, SkeletonRows } from '$lib/ui';

	let { children }: { children: Snippet } = $props();

	let conn = $state<Connection | null>(null);
	let link = $state<ConnectionStatus>('connecting');
	let notes = $state<NoteLike[]>([]);
	let memory = $state<MemoryLike[]>([]);
	let loading = $state(true);
	let err = $state('');
	let pulses = $state<{ id: string; at: number }[]>([]);

	const graph = $derived(buildGraph(notes, memory));
	/** The node the URL names: the open note, or the entry being looked at. */
	const selected = $derived.by(() => {
		const path = page.url.pathname;
		const open = page.url.searchParams.get('open');
		const entry = page.url.searchParams.get('entry');
		if (path.endsWith('/notes') && open) return `note:${open}`;
		if (path.endsWith('/memory') && entry) return `memory:${entry}`;
		return '';
	});
	const dropped = $derived(link === 'closed' || link === 'error');
	const meta = $derived(
		loading
			? 'reading'
			: dropped
				? 'link dropped · last known'
				: `${notes.length} note${notes.length === 1 ? '' : 's'} · ${memory.length} remembered`
	);

	async function loadNotes(): Promise<void> {
		if (!conn) return;
		try {
			const answer = await conn.client.command<{ notes: NoteLike[] }>({ type: 'jarvis/notes/list' });
			notes = answer.notes ?? [];
		} catch (e) {
			// No notes integration is an empty half of the graph, not a failure.
			if (!isUnsupported(e)) err = describeError(e);
		}
	}

	async function loadMemory(): Promise<void> {
		if (!conn) return;
		try {
			const answer = await conn.client.command<{ entries: MemoryLike[] }>({ type: 'jarvis/memory/list' });
			memory = answer.entries ?? [];
		} catch (e) {
			if (!isUnsupported(e)) err = describeError(e);
		}
	}

	async function loadAll(): Promise<void> {
		err = '';
		await Promise.all([loadNotes(), loadMemory()]);
		loading = false;
	}

	function pulse(ids: string[]): void {
		if (!ids.length) return;
		const at = performance.now();
		// Old pulses are dropped here rather than by a timer: the Graph decides
		// what is still lit, and this list only has to stay short.
		pulses = [...pulses.filter((p) => at - p.at < 10_000), ...ids.map((id) => ({ id, at }))];
	}

	/** A turn's `memory_used`, off the pipeline event the core mirrors onto the bus. */
	function memoryUsed(data: Record<string, any> | undefined): string[] {
		const used = data?.data?.intent_output?.response?.data?.memory_used;
		if (!Array.isArray(used)) return [];
		return used
			.map((entry) => (typeof entry === 'string' ? entry : entry?.id))
			.filter((id): id is string => typeof id === 'string' && id.length > 0)
			.map((id) => `memory:${id}`);
	}

	let subs: Subscription[] = [];

	async function connect(): Promise<void> {
		conn?.close();
		conn = null;
		loading = true;
		try {
			const live = await openConnection({ onStatus: (s) => (link = s) });
			conn = live;
			subs = [
				await live.client.subscribeEvents((event: BusEvent) => {
					if (event.data?.type !== 'intent-end') return;
					pulse(memoryUsed(event.data));
				}, 'voice_pipeline_event'),
				await live.client.subscribeEvents((event: BusEvent) => {
					const name = String(event.data?.name ?? '');
					if (!name.startsWith('note_')) return;
					pulse(touchedBy(name, event.data?.arguments, graph.nodes));
					// The note may be new, or renamed: re-read, and light it once it is here.
					void loadNotes().then(() => pulse(touchedBy(name, event.data?.arguments, graph.nodes)));
				}, 'jarvis_tool_finished'),
				await live.client.subscribeEvents(() => void loadMemory(), 'memory_changed')
			];
			await loadAll();
		} catch (e) {
			err = describeError(e);
			loading = false;
		}
	}

	// A section changed something: re-read, so the picture is what the lists are.
	$effect(() => {
		void knowledge.version;
		if (conn && !loading) void loadAll();
	});

	onMount(() => void connect());
	onDestroy(() => {
		for (const sub of subs) void sub.unsubscribe();
		conn?.close();
	});

	function select(id: string): void {
		const [kind, ...rest] = id.split(':');
		const key = rest.join(':');
		if (kind === 'note') void goto(`/knowledge/notes?open=${encodeURIComponent(key)}`, { noScroll: true });
		else if (kind === 'memory') void goto(`/knowledge/memory?entry=${encodeURIComponent(key)}`, { noScroll: true });
	}
</script>

<svelte:head><title>Jarvis · Knowledge</title></svelte:head>

<ScreenTitle
	title="Knowledge"
	lede="What Jarvis has written down, and what it remembers about you."
	testid="knowledge-screen"
/>

<div class="knowledge">
	<aside class="map">
		<Panel title="Graph" {meta} live={pulses.length > 0} testid="knowledge-graph">
			{#snippet children()}
				{#if loading}
					<div class="pad"><SkeletonRows rows={3} /></div>
				{:else if err}
					<p class="why" role="alert" data-testid="knowledge-graph-error">{err}</p>
				{:else}
					{#if dropped}
						<p class="why" data-testid="knowledge-graph-offline">
							The link dropped. This is the graph as it was; the section beside it can reconnect.
						</p>
					{/if}
					<div class="pad">
						<Graph nodes={graph.nodes} edges={graph.edges} {selected} {pulses} onselect={select} height={300} />
					</div>
					<p class="key" aria-hidden="true">
						<i class="note"></i> note <i class="mem"></i> remembered <i class="link"></i> link <i class="tag"></i> shared tag
					</p>
				{/if}
			{/snippet}
		</Panel>
	</aside>

	<section class="body">
		<SectionStrip sections={sectionsOf('/knowledge')} />
		{@render children()}
	</section>
</div>

<style>
	.knowledge {
		display: grid;
		grid-template-columns: calc(var(--jv-space-7) * 7.9) minmax(0, 1fr);
		gap: var(--jv-space-6);
		align-items: start;
	}
	.map {
		position: sticky;
		top: calc(var(--jv-space-7) + var(--jv-space-4));
		min-width: 0;
	}
	.map :global(.body) {
		padding: 0;
	}
	.pad {
		padding: var(--jv-space-3);
	}
	.why {
		margin: 0;
		padding: var(--jv-space-3) var(--jv-space-4);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.key {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2) var(--jv-space-3);
		align-items: center;
		margin: 0;
		padding: var(--jv-space-2) var(--jv-space-4) var(--jv-space-3);
		border-top: 1px solid var(--jv-line-hair);
		/* A legend is a label, not data: the body face, like every other label. */
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	.key i {
		display: inline-block;
		width: var(--jv-space-2);
		height: var(--jv-space-2);
		margin-right: var(--jv-space-1);
		vertical-align: middle;
	}
	.key i.note {
		border: 1px solid var(--jv-text-dim);
		border-radius: 50%;
	}
	.key i.mem {
		background: var(--jv-text-faint);
		border-radius: 50%;
	}
	.key i.link,
	.key i.tag {
		height: 1px;
		width: var(--jv-space-4);
		background: var(--jv-line);
	}
	.key i.tag {
		background: var(--jv-line-hair);
	}
	.body {
		min-width: 0;
	}
	@media (max-width: 1100px) {
		.knowledge {
			grid-template-columns: minmax(0, 1fr);
			gap: var(--jv-space-5);
		}
		.map {
			position: static;
		}
	}
</style>
