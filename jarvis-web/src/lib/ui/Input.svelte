<!--
@component
A single-line input, or a textarea when `rows` is set. Bind the value:

```svelte
<Input bind:value={name} placeholder="light.kitchen_lamp" />
<Input bind:value={json} rows={8} mono />
```
-->
<script lang="ts">
	interface Props {
		value?: string;
		placeholder?: string;
		/** More than one row makes it a textarea. */
		rows?: number;
		/** Monospace, for ids, JSON and anything typed exactly. */
		mono?: boolean;
		disabled?: boolean;
		invalid?: boolean;
		testid?: string;
		oninput?: (event: Event) => void;
	}
	let {
		value = $bindable(''),
		placeholder = '',
		rows = 1,
		mono = false,
		disabled = false,
		invalid = false,
		testid = '',
		oninput
	}: Props = $props();
</script>

{#if rows > 1}
	<textarea
		class="in"
		class:mono
		class:invalid
		{rows}
		{placeholder}
		{disabled}
		aria-invalid={invalid || undefined}
		data-testid={testid || undefined}
		bind:value
		{oninput}
	></textarea>
{:else}
	<input
		class="in"
		class:mono
		class:invalid
		type="text"
		{placeholder}
		{disabled}
		aria-invalid={invalid || undefined}
		data-testid={testid || undefined}
		bind:value
		{oninput}
	/>
{/if}

<style>
	.in {
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-3);
		width: 100%;
		transition: border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.in::placeholder {
		color: var(--jv-text-faint);
	}
	.in:hover:not(:disabled) {
		border-color: var(--jv-line);
	}
	.in:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.in:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.mono {
		font-family: var(--jv-font-chrome);
	}
	.invalid {
		border-color: var(--jv-danger);
	}
	textarea.in {
		line-height: 1.6;
		resize: vertical;
	}
</style>
