<script lang="ts">
	import type { Snippet } from 'svelte';
	import DestinationNav from '$lib/DestinationNav.svelte';
	import { sectionsOf } from '$lib/screens';

	let { children }: { children: Snippet } = $props();
</script>

<svelte:head><title>Jarvis · Work</title></svelte:head>

<h1>WORK</h1>
<p class="lede" data-testid="work-screen">What Jarvis is doing or has done for you.</p>

<DestinationNav sections={sectionsOf('/work')} />

{@render children()}
