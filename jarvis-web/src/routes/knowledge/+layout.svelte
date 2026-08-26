<script lang="ts">
	import type { Snippet } from 'svelte';
	import { ScreenTitle, SectionStrip } from '$lib/ui';
	import { sectionsOf } from '$lib/screens';

	let { children }: { children: Snippet } = $props();
</script>

<svelte:head><title>Jarvis · Knowledge</title></svelte:head>

<ScreenTitle title="Knowledge" lede="What Jarvis has written down, and what it remembers about you." testid="knowledge-screen" />

<SectionStrip sections={sectionsOf('/knowledge')} />

{@render children()}
