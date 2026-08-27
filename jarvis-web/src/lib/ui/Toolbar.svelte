<!--
@component
The strip above a list: controls on the left, controls on the right, wrapping
on a narrow screen instead of overflowing.

```svelte
<Toolbar>
	{#snippet children()}<Input bind:value={filter} />{/snippet}
	{#snippet end()}<Button variant="primary">New</Button>{/snippet}
</Toolbar>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	interface Props {
		children: Snippet;
		/** Pushed to the right-hand end. */
		end?: Snippet;
	}
	let { children, end }: Props = $props();
</script>

<div class="toolbar">
	<div class="start">{@render children()}</div>
	{#if end}<div class="end">{@render end()}</div>{/if}
</div>

<style>
	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
	}
	.start,
	.end {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
	}
</style>
