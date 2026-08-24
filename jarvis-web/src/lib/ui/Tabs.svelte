<!--
@component
Reactor II's tab strip: uppercase labels on a hairline with an underline that
slides to the selected one. A tab may carry a count and a live dot.

```svelte
<Tabs tabs={[{ id: 'all', label: 'All' }, { id: 'run', label: 'Running', count: 2, live: true }]}
	bind:selected onselect={(id) => goto(id)} />
```
-->
<script lang="ts">
	interface Tab {
		id: string;
		label: string;
		/** A number beside the label: how many things are in there. */
		count?: number;
		/** Something in this tab is happening now. */
		live?: boolean;
	}
	interface Props {
		tabs: Tab[];
		selected?: string;
		onselect?: (id: string) => void;
	}
	let { tabs, selected = $bindable(''), onselect }: Props = $props();
	let current = $derived(selected || tabs[0]?.id || '');

	function choose(id: string) {
		selected = id;
		onselect?.(id);
	}
</script>

<div class="tabs" role="tablist">
	{#each tabs as tab (tab.id)}
		<button
			class="tab"
			class:on={tab.id === current}
			role="tab"
			type="button"
			aria-selected={tab.id === current}
			data-testid="tab-{tab.id}"
			onclick={() => choose(tab.id)}
		>
			<span>{tab.label}</span>
			{#if tab.count !== undefined}<b>{tab.count}</b>{/if}
			{#if tab.live}<i class="dot" aria-hidden="true"></i>{/if}
		</button>
	{/each}
</div>

<style>
	.tabs {
		display: flex;
		gap: var(--jv-space-5);
		border-bottom: 1px solid var(--jv-line-hair);
		overflow-x: auto;
	}
	.tab {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		background: transparent;
		border: 0;
		border-bottom: 2px solid transparent;
		padding: var(--jv-space-3) 0;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		cursor: pointer;
		white-space: nowrap;
		transition: color var(--jv-dur-fast) var(--jv-ease-out),
			border-color var(--jv-dur-base) var(--jv-ease-out);
	}
	.tab:hover {
		color: var(--jv-text);
	}
	.tab:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.on {
		color: var(--jv-text-bright);
		border-bottom-color: var(--jv-accent);
	}
	b {
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		padding: 0 var(--jv-space-1);
	}
	.dot {
		width: var(--jv-space-1);
		height: var(--jv-space-1);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-sm);
	}
</style>
