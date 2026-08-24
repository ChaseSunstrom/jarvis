<!--
@component
One line in a list: a label on the left, a value or controls on the right, a
hairline underneath. `current` marks the one thing happening now.

```svelte
<Row label="first token" value="640 ms" current />
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	interface Props {
		label?: string;
		/** The right-hand side, when it is only text. */
		value?: string;
		/** This row is the live one: accent rule, brighter text. */
		current?: boolean;
		testid?: string;
		/** Anything richer than `value`. */
		children?: Snippet;
	}
	let { label = '', value = '', current = false, testid = '', children }: Props = $props();
</script>

<div class="row" class:current data-testid={testid || undefined}>
	{#if label}<span class="label">{label}</span>{/if}
	{#if children}
		<span class="slot">{@render children()}</span>
	{:else if value}
		<span class="value">{value}</span>
	{/if}
</div>

<style>
	.row {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--jv-space-3);
		padding: var(--jv-space-2) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.row:last-child {
		border-bottom: 0;
	}
	.current {
		color: var(--jv-text-bright);
		background: var(--jv-wash);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.value {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		font-variant-numeric: tabular-nums;
		color: var(--jv-text);
		white-space: nowrap;
	}
	.current .value {
		color: var(--jv-accent);
	}
</style>
