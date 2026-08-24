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
	input {
		position: absolute;
		opacity: 0;
		width: 0;
		height: 0;
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
