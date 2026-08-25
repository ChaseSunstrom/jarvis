<!--
@component
The console's action. One `variant` per job: `ghost` (the default — most
buttons on a page), `primary` (exactly one per screen: the thing the screen is
for), `danger` (destructive, and it says so in the label too), `approve` (the
yes half of a held action — the same shape in the OK colour, so saying yes and
saying no are not two different-looking controls).

```svelte
<Button onclick={save}>Save</Button>
<Button variant="primary" onclick={approve}>Approve</Button>
<Button variant="danger" disabled={!selected} title="Pick a row first">Delete</Button>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	interface Props extends Omit<HTMLButtonAttributes, 'class' | 'children'> {
		variant?: 'ghost' | 'primary' | 'danger' | 'approve';
		type?: 'button' | 'submit';
		disabled?: boolean;
		/** Why it is disabled, or what it will do. Shown on hover and to a reader. */
		title?: string;
		testid?: string;
		/**
		 * A toggle that is currently on.
		 *
		 * Lights the button the way `chrome.css`'s `button.btn.on` always has,
		 * and sets `aria-pressed`. It is here because two pages were keeping a
		 * raw `<button>` purely for a `class:on` directive — a toggle IS a
		 * button state, and expressing it here is the difference between the
		 * library covering the console and the console working around the
		 * library.
		 */
		pressed?: boolean;
		onclick?: (event: MouseEvent) => void;
		children: Snippet;
	}
	/*
	 * The rest go straight onto the element.
	 *
	 * `aria-expanded`, `aria-controls`, `aria-label`, `form` — an accessible
	 * control needs attributes this component has no opinion about, and a fixed
	 * Props list meant a page needing one of them kept a raw `<button>` and its
	 * own copy of the styling. Forwarding them is what let the last of those
	 * become `<Button>` (M48). `class` is deliberately NOT forwardable: the
	 * variant is the styling, and a page adding classes here is the one-off
	 * this component exists to prevent.
	 */
	let {
		variant = 'ghost',
		type = 'button',
		disabled = false,
		title = '',
		testid = '',
		pressed = undefined,
		onclick,
		children,
		...rest
	}: Props = $props();
</script>

<button
	class="btn {variant}"
	class:on={pressed}
	aria-pressed={pressed === undefined ? undefined : pressed}
	{type}
	{disabled}
	title={title || undefined}
	data-testid={testid || undefined}
	{onclick}
	{...rest}
>
	{@render children()}
</button>

<style>
	/* The yes half of a held action. Lifted out of `Approvals.svelte`, which
	   kept three raw <button>s purely to wear it. */
	.btn.approve {
		border-color: var(--jv-ok);
		color: var(--jv-ok);
	}

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
		transition: color var(--jv-dur-fast) var(--jv-ease-out),
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
