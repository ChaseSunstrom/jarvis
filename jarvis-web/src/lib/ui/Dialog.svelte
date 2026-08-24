<!--
@component
A modal question. Escape closes it, focus starts inside it, and the backdrop is
inert to a click — a dialog that vanishes when you brush past it is one that
loses an answer somebody meant to give.

```svelte
<Dialog open={confirming} title="Forget this repository?" onclose={() => (confirming = false)}>
	{#snippet children()}<p>Files stay on disk. Jarvis stops tracking it.</p>{/snippet}
</Dialog>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	interface Props {
		open?: boolean;
		title: string;
		onclose?: () => void;
		children: Snippet;
		/** The buttons. Rendered along the bottom. */
		actions?: Snippet;
	}
	let { open = false, title, onclose, children, actions }: Props = $props();

	function onkeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') onclose?.();
	}
</script>

<svelte:window on:keydown={open ? onkeydown : undefined} />

{#if open}
	<div class="scrim">
		<div class="dialog" role="dialog" aria-modal="true" aria-label={title} data-testid="dialog">
			<h2>{title}</h2>
			<div class="body">{@render children()}</div>
			{#if actions}<div class="actions">{@render actions()}</div>{/if}
		</div>
	</div>
{/if}

<style>
	.scrim {
		position: fixed;
		inset: 0;
		display: grid;
		place-items: center;
		padding: var(--jv-space-5);
		background: color-mix(in srgb, var(--jv-bg) 82%, transparent);
		z-index: 40;
	}
	.dialog {
		width: min(var(--jv-measure-dialog), 100%);
		background: var(--jv-panel);
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-md);
		box-shadow: var(--jv-elev-float);
		padding: var(--jv-space-5);
		display: grid;
		gap: var(--jv-space-4);
		animation: rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	h2 {
		margin: 0;
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-lg);
		color: var(--jv-text-bright);
	}
	.body {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--jv-space-3);
	}
	@keyframes rise {
		from {
			opacity: 0;
			transform: translateY(var(--jv-drift));
		}
		to {
			opacity: 1;
			transform: none;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.dialog {
			animation: none;
		}
	}
</style>
