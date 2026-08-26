<!--
@component
The section strip inside one destination: Reactor II's segmented control — a
hairline box, one segment per section, the current one raised on
`--jv-surface-2`. It lives in the destination's layout, persists while the
section under it changes, and every segment is a real link so a section has a
URL of its own.

It is deliberately not the top bar's tab strip. They look different because
they ARE different — one chooses a place, the other a view within it.

```svelte
<SectionStrip sections={sectionsOf('/house')} />
```
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

<nav class="strip" aria-label="Sections">
	{#each sections as section (section.path)}
		<a
			href={section.path}
			class:on={page.url.pathname === section.path}
			aria-current={page.url.pathname === section.path ? 'page' : undefined}
			data-testid="section-{section.name.toLowerCase().replace(/ /g, '-')}"
			title={section.purpose}>{section.label ?? section.name}</a
		>
	{/each}
</nav>

<style>
	.strip {
		display: inline-flex;
		max-width: 100%;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		overflow-x: auto;
		scrollbar-width: none;
		margin-bottom: var(--jv-space-5);
	}
	.strip::-webkit-scrollbar {
		display: none;
	}
	a {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		text-decoration: none;
		color: var(--jv-text-faint);
		padding: var(--jv-space-3) var(--jv-space-4);
		border-right: 1px solid var(--jv-line-hair);
		white-space: nowrap;
		transition: color var(--jv-dur-fast) var(--jv-ease-out), background var(--jv-dur-fast) var(--jv-ease-out);
	}
	a:last-child {
		border-right: 0;
	}
	a:hover {
		color: var(--jv-text);
	}
	a.on {
		color: var(--jv-text-bright);
		background: var(--jv-surface-2);
	}
	a:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: calc(-1 * var(--jv-focus-offset));
	}
</style>
