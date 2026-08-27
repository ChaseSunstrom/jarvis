<!--
@component
The newest moments — what Jarvis said while nobody was looking — as hairline
rows: the kind as a tag, the title, when. Newest first, and live: a moment
landing over the socket goes to the top. Nothing here is a control; the inbox
on the voice screen is where a moment is read and dismissed, and a dashboard
is read across a room.
```svelte
<Moments {moments} live />
```
-->
<script lang="ts">
	import { Pill } from '$lib/ui';
	import { ago, type MomentRow } from './widgets';

	interface Props {
		moments: MomentRow[];
		live?: boolean;
		/** For the "ago" column; a test pins the clock. */
		now?: number;
	}
	let { moments, live = false, now = Date.now() }: Props = $props();
</script>

{#if !moments.length}
	<p class="why" data-testid="moments-empty">
		No moments yet. Jarvis leaves one here when a task finishes, a briefing lands, or a page you
		watch changes.
	</p>
{:else}
	<div class="moments" data-testid="moments">
		{#each moments as moment, i (moment.id)}
			<div class="moment" class:read={moment.read} class:live={live && i === 0} data-testid="moment-{moment.id}" data-kind={moment.kind}>
				<Pill>{moment.kind}</Pill>
				<span class="title">{moment.title}</span>
				<span class="when">{moment.at ? ago(now / 1000 - moment.at) : ''}</span>
			</div>
		{/each}
	</div>
{/if}

<style>
	.moments {
		display: grid;
		min-height: 0;
		overflow: auto;
	}
	.moment {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		gap: var(--jv-space-3);
		align-items: baseline;
		padding: var(--jv-space-2) 0;
		border-top: 1px solid var(--jv-line-hair);
	}
	.moment:first-child {
		border-top: 0;
	}
	.title {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.moment.read .title {
		color: var(--jv-text-dim);
	}
	/* The newest, on the hero: the one line to read first. */
	.moment.live .title {
		color: var(--jv-text-bright);
	}
	.when {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
		white-space: nowrap;
	}
	.why {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
</style>
