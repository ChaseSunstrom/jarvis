<script lang="ts">
	// The pre-library skeleton, kept as a shim over `SkeletonRows` so the pages
	// that still import it render while they are migrated (M50). Delete this
	// file, and every import of it, once nothing references it.
	import { SkeletonRows } from '$lib/ui';
	interface Props {
		rows?: number;
		label?: string;
	}
	let { rows = 5, label = 'Loading' }: Props = $props();
</script>

<div data-testid="skeleton" role="status" aria-busy="true" aria-label={label}>
	<SkeletonRows {rows} />
</div>
