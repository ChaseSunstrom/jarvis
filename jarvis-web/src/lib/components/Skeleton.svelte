<script lang="ts">
	// Placeholder rows with the same rhythm as the real ones, so the page does
	// not jump when the data lands — and, more importantly, so an empty screen
	// never flashes "nothing here" at someone who is simply still connecting.
	interface Props {
		/** How many placeholder rows. */
		rows?: number;
		/** Announced while the real content loads. */
		label?: string;
	}
	let { rows = 5, label = 'Loading' }: Props = $props();
</script>

<div data-testid="skeleton" role="status" aria-busy="true" aria-label={label}>
	{#each Array.from({ length: rows }, (_, i) => i) as i (i)}
		<div class="jv-skeleton-row">
			<span
				class="jv-skeleton wide"
				style="max-width:{[16, 11, 14, 9, 13][i % 5]}rem"
				aria-hidden="true"
			></span>
			<span class="jv-skeleton pill" aria-hidden="true"></span>
			<span class="jv-skeleton btn" aria-hidden="true"></span>
		</div>
	{/each}
</div>
