<script lang="ts">
	import type { Snippet } from 'svelte';
	import { ScreenTitle, SectionStrip } from '$lib/ui';
	import { sectionsOf } from '$lib/screens';

	let { children }: { children: Snippet } = $props();
</script>

<svelte:head><title>Jarvis · Settings</title></svelte:head>

<ScreenTitle title="Settings" lede="What a person changes: the assistant and its models, its voice, the house, this console, and its tools." testid="settings-screen" />

<SectionStrip sections={sectionsOf('/settings')} />

{@render children()}
