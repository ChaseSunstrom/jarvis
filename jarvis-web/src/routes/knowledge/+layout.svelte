<script lang="ts">
	import type { Snippet } from 'svelte';
	import DestinationNav from '$lib/DestinationNav.svelte';
	import { sectionsOf } from '$lib/screens';

	let { children }: { children: Snippet } = $props();
</script>

<svelte:head><title>Jarvis · Knowledge</title></svelte:head>

<h1>KNOWLEDGE</h1>
<p class="lede" data-testid="knowledge-screen">What Jarvis has written down, and what it remembers about you.</p>

<DestinationNav sections={sectionsOf('/knowledge')} />

{@render children()}
