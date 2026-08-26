<script lang="ts">
	import type { Snippet } from 'svelte';
	import { ScreenTitle, SectionStrip } from '$lib/ui';
	import { sectionsOf } from '$lib/screens';

	let { children }: { children: Snippet } = $props();
</script>

<svelte:head><title>Jarvis · Settings</title></svelte:head>

<ScreenTitle title="Settings" lede="Configuration and capability: the assistant, what it can call, what is installed, and the machines it runs on." testid="settings-screen" />

<SectionStrip sections={sectionsOf('/settings')} />

{@render children()}
