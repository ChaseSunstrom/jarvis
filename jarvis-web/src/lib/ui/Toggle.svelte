<!--
@component
An on/off switch with its label. It is a real checkbox underneath, so it is
reachable by keyboard and announced as one.

```svelte
<Toggle bind:checked={exposed} label="Exposed to Jarvis" />
```
-->
<script lang="ts">
	interface Props {
		checked?: boolean;
		label: string;
		/** One line under the label: what turning it on actually does. */
		hint?: string;
		disabled?: boolean;
		testid?: string;
		onchange?: (event: Event) => void;
	}
	let {
		checked = $bindable(false),
		label,
		hint = '',
		disabled = false,
		testid = '',
		onchange
	}: Props = $props();
</script>

<label class="toggle" class:disabled>
	<input type="checkbox" bind:checked {disabled} data-testid={testid || undefined} {onchange} />
	<span class="track" aria-hidden="true"><span class="knob"></span></span>
	<span class="text">
		<span class="label">{label}</span>
		{#if hint}<span class="hint">{hint}</span>{/if}
	</span>
</label>

<style>
	.toggle {
		position: relative;
		display: grid;
		grid-template-columns: auto 1fr;
		align-items: center;
		gap: var(--jv-space-3);
		cursor: pointer;
	}
	.toggle.disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	/*
	 * The real checkbox, invisible but COVERING the control rather than
	 * collapsed to nothing in a corner.
	 *
	 * It used to be `width: 0; height: 0`, which works for a person (the label
	 * is clickable) and not for anything that addresses the input itself: a
	 * zero-size element cannot be clicked at a point, so a test given this
	 * component's `testid` timed out on "element is not stable". Covering the
	 * label means the same click, the same focus ring on the track, and one
	 * testid that both `toBeChecked()` and `click()` can use.
	 */
	input {
		position: absolute;
		inset: 0;
		/* ABOVE the track it is invisible behind. Without this the track — a
		   positioned element later in the DOM — wins the hit test, and a click
		   addressed to the control lands on the decoration instead. */
		z-index: 1;
		margin: 0;
		opacity: 0;
		cursor: inherit;
	}
	.track {
		display: block;
		width: var(--jv-space-6);
		height: var(--jv-space-4);
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-field);
		position: relative;
		transition: background var(--jv-dur-fast) var(--jv-ease-out),
			border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.knob {
		position: absolute;
		top: 1px;
		left: 1px;
		width: var(--jv-space-3);
		height: var(--jv-space-3);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-text-dim);
		transition: transform var(--jv-dur-base) var(--jv-ease-out),
			background var(--jv-dur-fast) var(--jv-ease-out);
	}
	input:checked + .track {
		border-color: var(--jv-accent);
		background: var(--jv-wash);
	}
	input:checked + .track .knob {
		background: var(--jv-accent);
		transform: translateX(var(--jv-space-4));
	}
	input:focus-visible + .track {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.text {
		display: grid;
		gap: 0;
	}
	.label {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
	}
	.hint {
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	@media (prefers-reduced-motion: reduce) {
		.knob {
			transition: none;
		}
	}
</style>
