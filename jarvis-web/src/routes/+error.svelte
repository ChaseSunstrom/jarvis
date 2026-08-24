<!--
	The page that renders when a route throws.

	Without this, SvelteKit's default error page appears — unstyled, in a
	different typeface, with a stack trace where the interface was. It is the one
	screen a user sees at their least patient moment, so it is on the design
	system like everything else and says the two things worth knowing: what
	happened, and the way back.
-->
<script lang="ts">
	import { page } from '$app/state';
	import { ErrorState, Button } from '$lib/ui';

	const detail = $derived(
		page.error?.message ? `${page.status} · ${page.error.message}` : `HTTP ${page.status}`
	);
</script>

<svelte:head><title>Jarvis · {page.status}</title></svelte:head>

<div class="wrap" data-testid="route-error">
	<ErrorState
		title={page.status === 404 ? 'There is no screen here' : 'This screen failed to load'}
		{detail}
		testid="error"
	/>
	<div class="ways">
		<Button onclick={() => location.reload()}>Reload</Button>
		<Button variant="primary" onclick={() => (location.href = '/devices')}>Back to the console</Button>
	</div>
</div>

<style>
	.wrap {
		display: grid;
		gap: var(--jv-space-4);
		max-width: 46ch;
		margin: var(--jv-space-7) auto;
		padding: 0 var(--jv-space-4);
	}
	.ways {
		display: flex;
		gap: var(--jv-space-3);
	}
</style>
