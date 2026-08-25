<!--
@component
The section strip inside one destination.

The console has four destinations now, not eleven, and each holds several
sections. This is the strip that switches between them: it persists across a
section change (it lives in the destination's layout), the URL changes, and the
content under it swaps.

It is deliberately not the same component as the top-level nav. They look
different because they ARE different — one chooses a place, the other chooses a
view within it — and drawing them identically was how the old console ended up
feeling like eleven unrelated pages.
-->
<script lang="ts">
	import { page } from '$app/state';
	import type { Screen } from '$lib/screens';

	interface Props {
		/** The sections of this destination, in order. */
		sections: Screen[];
	}
	let { sections }: Props = $props();
</script>

<nav class="sections" aria-label="Sections">
	{#each sections as section (section.path)}
		<a
			href={section.path}
			class:current={page.url.pathname === section.path}
			aria-current={page.url.pathname === section.path ? 'page' : undefined}
			data-testid="section-{section.name.toLowerCase().replace(/ /g, '-')}"
			title={section.purpose}>{section.name}</a
		>
	{/each}
</nav>

<style>
	.sections {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-1);
		margin-bottom: var(--jv-space-5);
		padding-bottom: var(--jv-space-2);
		border-bottom: 1px solid var(--jv-line-soft);
	}
	a {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		text-decoration: none;
		color: var(--jv-text-dim);
		padding: var(--jv-space-2) var(--jv-space-3);
		border-radius: var(--jv-radius-sm);
		transition: color var(--jv-dur-fast) var(--jv-ease-out),
			background var(--jv-dur-fast) var(--jv-ease-out);
	}
	a:hover {
		color: var(--jv-text);
		background: var(--jv-wash);
	}
	.current {
		color: var(--jv-accent);
		background: var(--jv-wash);
	}
</style>
