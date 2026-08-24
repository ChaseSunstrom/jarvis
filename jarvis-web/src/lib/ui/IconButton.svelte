<!--
@component
A button whose label is a glyph. The visible glyph is decoration; `label` is
the accessible name and the tooltip, and it is required — an icon-only control
with no name is a control nobody can describe.

```svelte
<IconButton label="Dismiss" glyph="×" onclick={close} />
```
-->
<script lang="ts">
	interface Props {
		/** What the button does, in words. Required. */
		label: string;
		/** The glyph to draw. Decorative — the label is what is announced. */
		glyph: string;
		disabled?: boolean;
		testid?: string;
		onclick?: (event: MouseEvent) => void;
	}
	let { label, glyph, disabled = false, testid = '', onclick }: Props = $props();
</script>

<button
	class="icon"
	type="button"
	{disabled}
	title={label}
	aria-label={label}
	data-testid={testid || undefined}
	{onclick}
>
	<span aria-hidden="true">{glyph}</span>
</button>

<style>
	.icon {
		display: grid;
		place-items: center;
		width: var(--jv-space-6);
		height: var(--jv-space-6);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
		background: transparent;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		cursor: pointer;
		transition: color var(--jv-dur-fast) var(--jv-ease-out),
			border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.icon:hover:not(:disabled) {
		color: var(--jv-text-bright);
		border-color: var(--jv-line);
	}
	.icon:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.icon:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
</style>
