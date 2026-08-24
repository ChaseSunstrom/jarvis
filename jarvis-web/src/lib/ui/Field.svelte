<!--
@component
A labelled control, with room for a hint and an error. The label is bound to
whatever the snippet renders, so the control does not have to remember to.

```svelte
<Field label="Name" hint="Shown in the console" error={nameError}>
	<Input bind:value={name} />
</Field>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	interface Props {
		label: string;
		hint?: string;
		/** Non-empty means the field is wrong, and says how. */
		error?: string;
		children: Snippet;
	}
	let { label, hint = '', error = '', children }: Props = $props();
</script>

<label class="field">
	<span class="label">{label}</span>
	{@render children()}
	{#if error}
		<span class="error" role="alert">{error}</span>
	{:else if hint}
		<span class="hint">{hint}</span>
	{/if}
</label>

<style>
	.field {
		display: grid;
		gap: var(--jv-space-1);
	}
	.label {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	.hint {
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.error {
		font-size: var(--jv-fs-2xs);
		color: var(--jv-danger-text);
	}
</style>
