<!--
@component
The day as a strip: one node per scheduled or recorded moment — done, running,
failed, still to come — with its time and a word. Reactor II draws it across
the top of WORK, so "did my seven o'clock reminder run" is a glance.

```svelte
<DayStrip nodes={[{ at: '07:00', label: 'briefing', state: 'done' }, { at: '13:10', label: 'pairing', state: 'error' }, { at: '02:39', label: 'offline', state: 'running' }]} />
```
-->
<script lang="ts">
	export interface DayNode {
		at: string;
		label: string;
		state: 'done' | 'error' | 'running' | 'pending';
		href?: string;
		testid?: string;
	}
	interface Props {
		nodes: DayNode[];
		label?: string;
		testid?: string;
	}
	let { nodes, label = 'Today', testid = 'day-strip' }: Props = $props();
</script>

<div class="strip" aria-label={label} data-testid={testid}>
	{#each nodes as node, i (node.at + node.label + i)}
		{#if node.href}
			<a class="n {node.state}" href={node.href} data-testid={node.testid || undefined}>
				<i aria-hidden="true"></i><span>{node.at} {node.label}</span>
			</a>
		{:else}
			<div class="n {node.state}" data-testid={node.testid || undefined}>
				<i aria-hidden="true"></i><span>{node.at} {node.label}</span>
			</div>
		{/if}
	{/each}
</div>

<style>
	.strip {
		display: flex;
		width: max-content;
		max-width: 100%;
		margin: 0 auto var(--jv-space-5);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		overflow-x: auto;
		scrollbar-width: none;
		animation: jv-rise var(--jv-dur-enter) var(--jv-ease-out) both;
	}
	.strip::-webkit-scrollbar {
		display: none;
	}
	.n {
		position: relative;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--jv-space-2);
		width: calc(var(--jv-space-7) * 2.6667);
		flex: none;
		text-decoration: none;
		color: inherit;
		white-space: nowrap;
	}
	.n::before {
		content: '';
		position: absolute;
		top: var(--jv-space-1);
		left: 50%;
		width: 100%;
		height: 1px;
		background: var(--jv-line-hair);
	}
	.n:last-child::before {
		display: none;
	}
	.n i {
		position: relative;
		z-index: 1;
		width: calc(var(--jv-space-2) + var(--jv-space-1));
		height: calc(var(--jv-space-2) + var(--jv-space-1));
		border-radius: 50%;
		border: 1px solid var(--jv-text-faint);
		background: var(--jv-bg);
	}
	.n.done i {
		background: var(--jv-text-dim);
		border-color: var(--jv-text-dim);
	}
	.n.error i {
		background: var(--jv-danger);
		border-color: var(--jv-danger);
	}
	.n.running i {
		background: var(--jv-accent);
		border-color: var(--jv-accent);
		box-shadow: 0 0 var(--jv-space-2) var(--jv-glow);
		animation: jv-blink var(--jv-dur-blink) var(--jv-ease-in-out) infinite;
	}
	.n.running span {
		color: var(--jv-accent);
	}
	a.n:hover span {
		color: var(--jv-text);
	}
</style>
