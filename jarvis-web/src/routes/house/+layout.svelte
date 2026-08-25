<script lang="ts">
	import type { Snippet } from 'svelte';
	import DestinationNav from '$lib/DestinationNav.svelte';
	import { sectionsOf } from '$lib/screens';

	let { children }: { children: Snippet } = $props();
</script>

<svelte:head><title>Jarvis · House</title></svelte:head>

<h1>HOUSE</h1>
<p class="lede" data-testid="house-screen">
	What is on, where it is, what it has been doing, and the rules that run themselves.
</p>

<DestinationNav sections={sectionsOf('/house')} />

{@render children()}
