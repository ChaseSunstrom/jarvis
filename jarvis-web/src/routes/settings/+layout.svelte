<script lang="ts">
	import type { Snippet } from 'svelte';
	import DestinationNav from '$lib/DestinationNav.svelte';
	import { sectionsOf } from '$lib/screens';

	let { children }: { children: Snippet } = $props();
</script>

<svelte:head><title>Jarvis · Settings</title></svelte:head>

<h1>SETTINGS</h1>
<p class="lede" data-testid="settings-screen">Configuration and capability: the assistant, what it can call, what is installed, and the machines it runs on.</p>

<DestinationNav sections={sectionsOf('/settings')} />

{@render children()}
