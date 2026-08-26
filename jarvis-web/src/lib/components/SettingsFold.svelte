<!--
@component
A fold: a flat panel whose head is its own disclosure. SETTINGS puts the raw
rows behind one called EVERYTHING and the event stream behind another, so the
few rows a person came for are what the page opens on and the rest is one
click in — never gone.

```svelte
<SettingsFold title="Everything" meta="14 settings" testid="everything">
	{#snippet children()}…{/snippet}
</SettingsFold>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	interface Props {
		title: string;
		/** The right-hand side of the head: a count, a filter, a word. */
		meta?: string;
		open?: boolean;
		testid?: string;
		/** A `data-testid` for the head, so a test can open the fold by name. */
		summaryTestid?: string;
		children: Snippet;
	}
	let { title, meta = '', open = $bindable(false), testid = '', summaryTestid = '', children }: Props = $props();
</script>

<details class="fold" bind:open data-testid={testid || undefined}>
	<summary data-testid={summaryTestid || undefined}>
		<span>{title}</span>
		{#if meta}<span class="meta">{meta}</span>{/if}
	</summary>
	<div class="fold-body">{@render children()}</div>
</details>

<style>
	.fold {
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		overflow: hidden;
	}
	.fold > summary {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-4);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		cursor: pointer;
		list-style: none;
		transition: color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.fold > summary:hover {
		color: var(--jv-text);
	}
	.fold > summary::-webkit-details-marker {
		display: none;
	}
	.fold > summary::after {
		content: '▸';
		color: var(--jv-text-faint);
		transition: transform var(--jv-dur-fast) var(--jv-ease-out);
	}
	.fold[open] > summary {
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.fold[open] > summary::after {
		transform: rotate(90deg);
	}
	.meta {
		margin-left: auto;
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		letter-spacing: var(--jv-track-tight);
		text-transform: none;
		color: var(--jv-text-faint);
	}
	.fold-body {
		padding: var(--jv-space-4);
	}
	@media (prefers-reduced-motion: reduce) {
		.fold > summary::after,
		.fold > summary {
			transition: none;
		}
	}
</style>
