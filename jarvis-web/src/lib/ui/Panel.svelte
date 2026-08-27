<!--
@component
A flat surface with a hairline edge and an optional head. Reactor II has no
glass: a panel is a rectangle of `--jv-panel` with a `--jv-line-hair` border,
and depth comes from the hairline, not a shadow.

```svelte
<Panel title="Plan" meta="step 3 of 5">
	{#snippet children()}<ol>…</ol>{/snippet}
</Panel>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	interface Props {
		/** The head's label. Omit for a panel with no head. */
		title?: string;
		/** The right-hand side of the head: a count, a duration, a status. */
		meta?: string;
		/** Draw `meta` in the accent, for something happening now. */
		live?: boolean;
		testid?: string;
		children: Snippet;
	}
	let { title = '', meta = '', live = false, testid = '', children }: Props = $props();
</script>

<section class="panel" data-testid={testid || undefined}>
	{#if title}
		<h2 class="head">
			<span>{title}</span>
			{#if meta}<b class:live>{meta}</b>{/if}
		</h2>
	{/if}
	<div class="body">{@render children()}</div>
</section>

<style>
	.panel {
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		overflow: hidden;
	}
	.head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--jv-space-3);
		margin: 0;
		padding: var(--jv-space-3) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	.head b {
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		letter-spacing: var(--jv-track-tight);
		text-transform: none;
		color: var(--jv-text-faint);
	}
	.head b.live {
		color: var(--jv-accent);
	}
	.body {
		padding: var(--jv-space-4);
	}
</style>
