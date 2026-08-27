<!--
@component
A destination's head: the title in the display face and one sentence under it
in the body face, then whatever sits beside them (a range control, the one
primary action). Reactor II's dashboard title, generalised — every destination
opens the same way so the eye knows where it is.

```svelte
<ScreenTitle title="House" lede="What is on, where it is, and the rules that run themselves." testid="house-screen">
	{#snippet end()}<Button variant="primary">+ Widget</Button>{/snippet}
</ScreenTitle>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	interface Props {
		title: string;
		lede?: string;
		/** The `data-testid` the destination's probe wants, on the lede. */
		testid?: string;
		end?: Snippet;
	}
	let { title, lede = '', testid = '', end }: Props = $props();
</script>

<header class="head">
	<div>
		<h1>{title}</h1>
		{#if lede}<p class="lede" data-testid={testid || undefined}>{lede}</p>{/if}
	</div>
	{#if end}<div class="end">{@render end()}</div>{/if}
</header>

<style>
	.head {
		display: flex;
		align-items: flex-end;
		justify-content: space-between;
		gap: var(--jv-space-4);
		flex-wrap: wrap;
		margin-bottom: var(--jv-space-5);
		animation: jv-rise var(--jv-dur-enter) var(--jv-ease-out) both;
	}
	h1 {
		margin: 0;
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-2xl);
		line-height: 1.1;
		color: var(--jv-text-bright);
	}
	.lede {
		margin: var(--jv-space-1) 0 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
		max-width: 70ch;
	}
	.end {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
	}
</style>
