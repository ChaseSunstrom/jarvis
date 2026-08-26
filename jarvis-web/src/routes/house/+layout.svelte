<script lang="ts">
	import type { Snippet } from 'svelte';
	import { ScreenTitle, SectionStrip } from '$lib/ui';
	import { sectionsOf } from '$lib/screens';

	let { children }: { children: Snippet } = $props();
</script>

<svelte:head><title>Jarvis · House</title></svelte:head>

<ScreenTitle title="House" lede="What is on, where it is, what it has been doing, and the rules that run themselves." testid="house-screen" />

<SectionStrip sections={sectionsOf('/house')} />

{@render children()}
