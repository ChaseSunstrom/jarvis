<script lang="ts">
	import type { Snippet } from 'svelte';
	import { ScreenTitle, SectionStrip } from '$lib/ui';
	import { sectionsOf } from '$lib/screens';

	let { children }: { children: Snippet } = $props();
</script>

<svelte:head><title>Jarvis · Work</title></svelte:head>

<ScreenTitle title="Work" lede="What Jarvis is doing or has done for you: tasks, research, coding jobs." testid="work-screen" />

<SectionStrip sections={sectionsOf('/work')} />

{@render children()}
