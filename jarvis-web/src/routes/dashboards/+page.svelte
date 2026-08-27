<script lang="ts">
	/**
	 * The dashboard, a destination of its own (M62).
	 *
	 * The other destinations are a layout, a section strip and a redirect to
	 * their first section; this one has no sections, so the page is the
	 * destination and `Dashboards.svelte` — the section it was under HOUSE
	 * from M48 to M61 — is what it draws. `/house/dashboards` redirects here.
	 */
	import { ScreenTitle } from '$lib/ui';
	import Dashboards from '$lib/sections/Dashboards.svelte';
</script>

<svelte:head><title>Jarvis · Dashboards</title></svelte:head>

<ScreenTitle title="Dashboards" lede="The house at a glance: the graphs and readings somebody arranged." testid="dashboards-title" />

<Dashboards />
