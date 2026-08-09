<script lang="ts">
	// The toast rail. Reads the plain observable in `$lib/toast` — pages and the
	// command palette both write to it, so a failed `call_service` is loud
	// wherever it was issued from.
	//
	// `aria-live="polite"` rather than assertive: a toast is a report on
	// something the user just did, not an interruption. Errors get
	// `role="alert"` on the individual toast, which does interrupt.
	import { onMount } from 'svelte';
	import { toasts, type Toast } from '$lib/toast';

	let items = $state<Toast[]>([]);

	onMount(() => toasts.subscribe((next) => (items = next)));
</script>

<div class="jv-toasts" aria-live="polite" aria-label="Notifications">
	{#each items as toast (toast.id)}
		<div
			class="jv-toast"
			data-kind={toast.kind}
			data-testid="toast"
			role={toast.kind === 'error' ? 'alert' : 'status'}
		>
			<div class="jv-toast-body">
				<p class="jv-toast-text">{toast.text}</p>
				{#if toast.detail}<span class="jv-toast-detail">{toast.detail}</span>{/if}
			</div>
			<button
				type="button"
				class="jv-toast-close"
				data-testid="toast-dismiss"
				aria-label="Dismiss notification"
				onclick={() => toasts.dismiss(toast.id)}>×</button
			>
		</div>
	{/each}
</div>
