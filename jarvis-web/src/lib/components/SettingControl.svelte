<!--
@component
The control for one setting, by its type: a `Select` for a choice or a
boolean, an `Input` otherwise. Shared by the plain row and the raw row so the
two cannot disagree about how a number is typed or a choice is offered.

```svelte
<SettingControl {store} {row} testid="input-{row.key}" />
```
-->
<script lang="ts">
	import type { SettingRow } from '$lib/jarvisClient';
	import type { SettingsStore } from '$lib/settingsStore.svelte';
	import { Input, Select } from '$lib/ui';

	interface Props {
		store: SettingsStore;
		row: SettingRow;
		testid: string;
		disabled?: boolean;
	}
	let { store, row, testid, disabled = false }: Props = $props();
</script>

{#if row.type === 'choice' && row.choices?.length}
	<Select
		value={store.draftOf(row)}
		{testid}
		{disabled}
		options={store.choicesOf(row)}
		onchange={(e) => store.setDraft(row.key, (e.currentTarget as HTMLSelectElement).value)}
	/>
{:else if row.type === 'boolean'}
	<Select
		value={store.draftOf(row)}
		{testid}
		{disabled}
		options={[
			{ value: 'true', label: 'on' },
			{ value: 'false', label: 'off' }
		]}
		onchange={(e) => store.setDraft(row.key, (e.currentTarget as HTMLSelectElement).value)}
	/>
{:else}
	<Input
		value={store.draftOf(row)}
		{testid}
		{disabled}
		mono={row.type !== 'string'}
		oninput={(e) => store.setDraft(row.key, (e.currentTarget as HTMLInputElement).value)}
	/>
{/if}
