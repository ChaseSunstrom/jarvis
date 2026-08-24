<!--
@component
A choice from a fixed list. Options are `{ value, label }`; bind the value.

```svelte
<Select bind:value={mode} options={[{ value: 'quick', label: 'Quick' }]} />
```
-->
<script lang="ts">
	interface Option {
		value: string;
		label: string;
	}
	interface Props {
		value?: string;
		options: Option[];
		disabled?: boolean;
		testid?: string;
		onchange?: (event: Event) => void;
	}
	let {
		value = $bindable(''),
		options,
		disabled = false,
		testid = '',
		onchange
	}: Props = $props();
</script>

<select
	class="sel"
	{disabled}
	data-testid={testid || undefined}
	bind:value
	{onchange}
>
	{#each options as option (option.value)}
		<option value={option.value}>{option.label}</option>
	{/each}
</select>

<style>
	.sel {
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-3);
	}
	.sel:hover:not(:disabled) {
		border-color: var(--jv-line);
	}
	.sel:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.sel:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
</style>
