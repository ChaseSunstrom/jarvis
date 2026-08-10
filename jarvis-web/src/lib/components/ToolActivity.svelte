<script lang="ts">
	/**
	 * What Jarvis is doing, while it does it.
	 *
	 * A turn that called five tools and took nine seconds used to show a
	 * spinner. Tool calls are the most interesting thing a turn does — they are
	 * the moment the assistant touches the house — and they were the least
	 * visible. jarvis-core now fires `jarvis_tool_started` and
	 * `jarvis_tool_finished` around each one; this draws them.
	 *
	 * The progress bar is real. `index` and `total` come from the model's own
	 * call list for the round, so "3 of 5" means three of the five things it
	 * asked for are done. A bar that animated on a timer would be a decoration
	 * that lies during exactly the nine seconds anybody is looking at it.
	 *
	 * Rows persist for a moment after the turn so you can read what happened,
	 * then fade. A failed call stays longer and keeps its reason: that one is
	 * not a progress report, it is an answer.
	 */
	import { onDestroy } from 'svelte';
	import type { Connection } from '$lib/connection';
	import type { BusEvent, Subscription } from '$lib/jarvisClient';

	let { conn }: { conn: Connection | null } = $props();

	type Row = {
		key: string;
		name: string;
		arguments: Record<string, unknown>;
		index: number;
		total: number;
		state: 'running' | 'ok' | 'failed';
		error?: string | null;
		durationMs?: number;
	};

	let rows = $state<Row[]>([]);
	let clearAt: ReturnType<typeof setTimeout> | null = null;

	/** How many of this round's calls have finished. */
	const done = $derived(rows.filter((r) => r.state !== 'running').length);
	const total = $derived(rows.length ? Math.max(...rows.map((r) => r.total), rows.length) : 0);
	const percent = $derived(total ? Math.round((done / total) * 100) : 0);
	const running = $derived(rows.some((r) => r.state === 'running'));
	const failed = $derived(rows.filter((r) => r.state === 'failed').length);

	function keyOf(data: { name?: string; round?: number; index?: number }): string {
		return `${data.round ?? 0}:${data.index ?? 0}:${data.name ?? '?'}`;
	}

	/** A one-line summary of the arguments, for a row that has to stay one line. */
	function summarise(args: Record<string, unknown>): string {
		const parts: string[] = [];
		for (const [key, value] of Object.entries(args ?? {})) {
			if (value === null || value === undefined || value === '') continue;
			const text =
				typeof value === 'object' ? JSON.stringify(value) : String(value);
			parts.push(`${key}: ${text.length > 40 ? `${text.slice(0, 39)}…` : text}`);
			if (parts.length >= 3) break;
		}
		return parts.join(' · ');
	}

	function scheduleClear(): void {
		if (clearAt) clearTimeout(clearAt);
		// Longer when something failed: a red row is an answer to read, not a
		// progress report to glance at.
		clearAt = setTimeout(
			() => {
				rows = [];
				clearAt = null;
			},
			failed ? 12_000 : 4_000
		);
	}

	$effect(() => {
		const connection = conn;
		if (!connection) return;
		let disposed = false;
		const subs: Subscription[] = [];

		(async () => {
			try {
				subs.push(
					await connection.client.subscribeEvents((event: BusEvent) => {
						const data = (event.data ?? {}) as Record<string, any>;
						if (clearAt) {
							clearTimeout(clearAt);
							clearAt = null;
						}
						rows = [
							...rows.filter((r) => r.key !== keyOf(data)),
							{
								key: keyOf(data),
								name: String(data.name ?? 'tool'),
								arguments: (data.arguments ?? {}) as Record<string, unknown>,
								index: Number(data.index ?? 0),
								total: Number(data.total ?? 1),
								state: 'running' as const
							}
						].sort((a, b) => a.index - b.index);
					}, 'jarvis_tool_started')
				);
				subs.push(
					await connection.client.subscribeEvents((event: BusEvent) => {
						const data = (event.data ?? {}) as Record<string, any>;
						const key = keyOf(data);
						rows = rows.map((row) =>
							row.key === key
								? {
										...row,
										state: data.ok ? ('ok' as const) : ('failed' as const),
										error: data.error ?? null,
										durationMs: Number(data.duration_ms ?? 0)
									}
								: row
						);
						if (!rows.some((r) => r.state === 'running')) scheduleClear();
					}, 'jarvis_tool_finished')
				);
			} catch {
				// An older jarvis-core does not fire these. Nothing to show is
				// the right outcome, and not worth an error banner over.
			}
			if (disposed) for (const sub of subs) void sub.unsubscribe();
		})();

		return () => {
			disposed = true;
			for (const sub of subs) void sub.unsubscribe();
		};
	});

	onDestroy(() => {
		if (clearAt) clearTimeout(clearAt);
	});
</script>

{#if rows.length}
	<section class="tools" data-testid="tool-activity" aria-live="polite">
		<header>
			<span class="label">{running ? 'WORKING' : failed ? 'FINISHED WITH ERRORS' : 'DONE'}</span>
			<span class="count" data-testid="tool-progress-count">{done} / {total}</span>
		</header>

		<div
			class="track"
			role="progressbar"
			aria-valuenow={percent}
			aria-valuemin="0"
			aria-valuemax="100"
			aria-label="Tool calls completed"
		>
			<div class="fill" class:failed={failed > 0} style="width: {percent}%"></div>
		</div>

		<ul>
			{#each rows as row (row.key)}
				<li class={row.state} data-testid="tool-row-{row.name}">
					<span class="dot" aria-hidden="true"></span>
					<span class="name">{row.name}</span>
					<span class="args">{summarise(row.arguments)}</span>
					{#if row.state === 'running'}
						<span class="meta">…</span>
					{:else if row.state === 'failed'}
						<span class="meta err" data-testid="tool-error-{row.name}">{row.error ?? 'failed'}</span>
					{:else}
						<span class="meta">{row.durationMs}ms</span>
					{/if}
				</li>
			{/each}
		</ul>
	</section>
{/if}

<style>
	.tools {
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-sm);
		background: var(--jv-panel);
		padding: var(--jv-space-3);
		margin-bottom: var(--jv-space-3);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	header {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-dim);
		margin-bottom: var(--jv-space-2);
	}
	.count {
		font-variant-numeric: tabular-nums;
		color: var(--jv-accent);
	}
	.track {
		height: 3px;
		border-radius: 2px;
		background: var(--jv-line-hair);
		overflow: hidden;
	}
	.fill {
		height: 100%;
		background: linear-gradient(90deg, var(--jv-accent-deep), var(--jv-accent));
		/* The width is the truth; the transition only stops it snapping. */
		transition: width var(--jv-dur-base) var(--jv-ease-out);
	}
	.fill.failed {
		background: linear-gradient(90deg, var(--jv-warn), var(--jv-danger));
	}
	ul {
		list-style: none;
		margin: var(--jv-space-2) 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	li {
		display: grid;
		grid-template-columns: 10px minmax(0, auto) minmax(0, 1fr) auto;
		align-items: baseline;
		gap: var(--jv-space-2);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		animation: jv-rise var(--jv-dur-fast) var(--jv-ease-out) both;
	}
	.dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--jv-line);
		justify-self: center;
	}
	li.running .dot {
		background: var(--jv-accent);
		animation: jv-pulse 1s ease-in-out infinite;
	}
	li.ok .dot {
		background: var(--jv-ok);
	}
	li.failed .dot {
		background: var(--jv-danger);
	}
	.name {
		color: var(--jv-text);
		font-weight: 600;
	}
	.args,
	.meta {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.meta {
		font-variant-numeric: tabular-nums;
		color: var(--jv-text-faint);
	}
	.meta.err {
		color: var(--jv-danger-text);
	}

	@keyframes jv-pulse {
		0%,
		100% {
			opacity: 1;
			transform: scale(1);
		}
		50% {
			opacity: 0.35;
			transform: scale(0.7);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.tools,
		li {
			animation: none;
		}
		li.running .dot {
			animation: none;
		}
		.fill {
			transition: none;
		}
	}
</style>
