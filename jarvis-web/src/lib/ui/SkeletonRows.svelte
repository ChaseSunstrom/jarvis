<!--
@component
Placeholder rows with the rhythm of the real ones, so the page does not jump
when the data lands — and so an empty screen never flashes "nothing here" at
somebody who is still connecting.

```svelte
<SkeletonRows rows={4} label="Loading tasks" />
```
-->
<script lang="ts">
	interface Props {
		rows?: number;
		/** Announced while the real content loads. */
		label?: string;
	}
	let { rows = 4, label = 'Loading' }: Props = $props();
	const widths = ['62%', '45%', '55%', '38%', '50%'];
</script>

<div class="skeleton" role="status" aria-busy="true" aria-label={label} data-testid="skeleton">
	{#each Array.from({ length: rows }, (_, i) => i) as i (i)}
		<div class="row">
			<span class="bar" style:width={widths[i % widths.length]} aria-hidden="true"></span>
			<span class="bar short" aria-hidden="true"></span>
		</div>
	{/each}
</div>

<style>
	.skeleton {
		display: grid;
		gap: var(--jv-space-2);
	}
	.row {
		display: flex;
		justify-content: space-between;
		gap: var(--jv-space-3);
		padding: var(--jv-space-2) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.bar {
		display: block;
		height: var(--jv-space-3);
		border-radius: var(--jv-radius-sm);
		background: var(--jv-line-soft);
		animation: pulse var(--jv-dur-pulse) var(--jv-ease-in-out) infinite alternate;
	}
	.short {
		width: var(--jv-space-7);
	}
	@keyframes pulse {
		from {
			opacity: 0.45;
		}
		to {
			opacity: 0.9;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.bar {
			animation: none;
		}
	}
</style>
