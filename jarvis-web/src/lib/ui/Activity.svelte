<!--
@component
The activity strip: what Jarvis is doing, as hairline rows — newest first.

	Each row is one thing (`$lib/activity`): the kind as a tag, the one line to
	read, the datum in mono, and a state dot that pulses while the thing is
	live. Only the newest row enters with motion (`--jv-dur-enter`); the rest
	are still, because a strip where everything moves says nothing. Under
	reduced motion nothing moves at all. The strip is a glance at the present:
	it is capped by the store, not by this component.
-->
<script lang="ts">
	import type { ActivityRow } from '$lib/activity.svelte';
	import Pill from './Pill.svelte';

	interface Props {
		rows: ActivityRow[];
		/** What the empty strip says: what would be here and how it gets here. */
		empty?: string;
		testid?: string;
	}
	let {
		rows,
		empty = 'Nothing happening. Ask for something and the work shows here as it happens.',
		testid = 'activity'
	}: Props = $props();

	const tone = (row: ActivityRow) =>
		row.state === 'failed' ? 'danger' : row.state === 'live' ? 'live' : 'neutral';
</script>

<div class="strip" data-testid={testid} data-count={rows.length} aria-live="polite" aria-label="Activity">
	{#if rows.length}
		<ol class="rows">
			{#each rows as row, i (row.id)}
				<li
					class="row {row.kind}"
					class:newest={i === 0}
					data-kind={row.kind}
					data-state={row.state}
					data-testid="activity-row-{row.kind}"
				>
					<span class="dot" class:live={row.state === 'live'} class:failed={row.state === 'failed'} aria-hidden="true"></span>
					<Pill tone={tone(row)}>{row.kind}</Pill>
					<span class="title">{row.title}</span>
					{#if row.detail}<span class="detail" data-mono>{row.detail}</span>{/if}
				</li>
			{/each}
		</ol>
	{:else}
		<p class="none">{empty}</p>
	{/if}
</div>

<style>
	.strip {
		width: 100%;
		min-width: 0;
	}
	.rows {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
	}
	.row {
		display: grid;
		grid-template-columns: auto auto minmax(0, 1fr) auto;
		align-items: center;
		gap: var(--jv-space-2);
		padding: var(--jv-space-2) 0;
		border-top: 1px solid var(--jv-line-hair);
		min-width: 0;
	}
	.row:first-child {
		border-top: 0;
	}
	/* Only the newest row moves: it enters from below over --jv-dur-enter. */
	.row.newest {
		animation: enter var(--jv-dur-enter) var(--jv-ease-out) both;
	}
	@keyframes enter {
		from {
			opacity: 0;
			transform: translateY(var(--jv-space-2));
		}
		to {
			opacity: 1;
			transform: none;
		}
	}
	.dot {
		width: var(--jv-space-2);
		height: var(--jv-space-2);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-tick);
	}
	.dot.live {
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-sm);
		animation: pulse var(--jv-dur-pulse) var(--jv-ease-in-out) infinite alternate;
	}
	.dot.failed {
		background: var(--jv-danger);
	}
	@keyframes pulse {
		from {
			opacity: 0.55;
		}
		to {
			opacity: 1;
		}
	}
	.title {
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		min-width: 0;
	}
	.row.live .title,
	.row.moment .title {
		color: var(--jv-text-bright);
	}
	.detail {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		max-width: 40%;
	}
	.none {
		margin: 0;
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	@media (prefers-reduced-motion: reduce) {
		.row.newest,
		.dot.live {
			animation: none;
		}
	}
</style>
