<!--
@component
The console's action. One `variant` per job: `ghost` (the default — most
buttons on a page), `primary` (exactly one per screen: the thing the screen is
for), `danger` (destructive, and it says so in the label too).

```svelte
<Button onclick={save}>Save</Button>
<Button variant="primary" onclick={approve}>Approve</Button>
<Button variant="danger" disabled={!selected} title="Pick a row first">Delete</Button>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	interface Props {
		variant?: 'ghost' | 'primary' | 'danger';
		type?: 'button' | 'submit';
		disabled?: boolean;
		/** Why it is disabled, or what it will do. Shown on hover and to a reader. */
		title?: string;
		testid?: string;
		onclick?: (event: MouseEvent) => void;
		children: Snippet;
	}
	let {
		variant = 'ghost',
		type = 'button',
		disabled = false,
		title = '',
		testid = '',
		onclick,
		children
	}: Props = $props();
</script>

<button
	class="btn {variant}"
	{type}
	{disabled}
	title={title || undefined}
	data-testid={testid || undefined}
	{onclick}
>
	{@render children()}
</button>

<style>
	.btn {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		background: transparent;
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-4);
		cursor: pointer;
		white-space: nowrap;
		transition:
			color var(--jv-dur-fast) var(--jv-ease-out),
			border-color var(--jv-dur-fast) var(--jv-ease-out),
			background var(--jv-dur-fast) var(--jv-ease-out);
	}
	.btn:hover:not(:disabled) {
		color: var(--jv-text-bright);
		border-color: var(--jv-text-dim);
	}
	.btn:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.btn:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	.primary {
		color: var(--jv-accent-ink);
		background: var(--jv-accent);
		border-color: var(--jv-accent);
	}
	.primary:hover:not(:disabled) {
		color: var(--jv-accent-ink);
		background: var(--jv-accent-lift);
		border-color: var(--jv-accent-lift);
	}
	.danger {
		color: var(--jv-danger-text);
		border-color: color-mix(in srgb, var(--jv-danger) 45%, transparent);
	}
	.danger:hover:not(:disabled) {
		color: var(--jv-danger-text);
		border-color: var(--jv-danger);
		background: color-mix(in srgb, var(--jv-danger) 10%, transparent);
	}
</style>
