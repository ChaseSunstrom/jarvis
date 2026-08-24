<!--
@component
Nothing here yet, and what would put something here. Never a bare blank: the
title says what is missing, the body says how it arrives.

```svelte
<EmptyState title="Nothing running" body="Ask Jarvis for something, or schedule one." />
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	interface Props {
		title: string;
		/** One sentence: how something gets here. */
		body?: string;
		testid?: string;
		/** A control that would fill it, when there is one. */
		action?: Snippet;
	}
	let { title, body = '', testid = '', action }: Props = $props();
</script>

<div class="empty" data-testid={testid || undefined} data-state="empty">
	<span class="mark" aria-hidden="true">[ ]</span>
	<p class="title">{title}</p>
	{#if body}<p class="body">{body}</p>{/if}
	{#if action}<div class="action">{@render action()}</div>{/if}
</div>

<style>
	.empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--jv-space-2);
		padding: var(--jv-space-6) var(--jv-space-4);
		text-align: center;
		border: 1px dashed var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		background: var(--jv-panel);
	}
	.mark {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-lg);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-accent-deep);
	}
	.title {
		margin: 0;
		font-size: var(--jv-fs-md);
		color: var(--jv-text-bright);
	}
	.body {
		margin: 0;
		max-width: 46ch;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.action {
		margin-top: var(--jv-space-2);
	}
</style>
