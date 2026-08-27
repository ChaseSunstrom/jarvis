<!--
@component
One setting exactly as the server describes it: its label, its key, the
control, where its value came from (`yaml` · `overlay` · `package` ·
`default`), SAVE and RESET, and the server's own note. This is the row the
settings page used to be made of; it lives behind EVERYTHING now, unchanged,
so nothing a person could once reach has gone anywhere.

```svelte
<SettingRaw {store} {row} />
```
-->
<script lang="ts">
	import type { SettingRow } from '$lib/jarvisClient';
	import type { SettingsStore } from '$lib/settingsStore.svelte';
	import { Button, Pill, SettingRow as SettingRowUi } from '$lib/ui';
	import SettingControl from './SettingControl.svelte';

	interface Props {
		store: SettingsStore;
		row: SettingRow;
	}
	let { store, row }: Props = $props();

	const locked = $derived(row.source === 'package');
	const busy = $derived(store.busyKey === row.key);
	const dirty = $derived(store.isDirty(row));
</script>

<SettingRowUi
	testid="setting-{row.key}"
	live={dirty}
	noted={Boolean(row.note || row.unapplied_reason || locked || store.fieldError[row.key])}
>
	{#snippet what()}
		<b>{row.label}</b>
		<code>{row.key}</code>
	{/snippet}
	<SettingControl {store} {row} testid="input-{row.key}" disabled={locked} />
	{#snippet acts()}
		<Pill tone={row.source === 'overlay' ? 'live' : 'neutral'} testid="source-{row.key}">{row.source}</Pill>
		<Button
			variant={dirty ? 'primary' : 'ghost'}
			testid="save-{row.key}"
			disabled={locked || busy || !dirty}
			title={locked
				? 'This setting is fixed in configuration.yaml'
				: busy
					? 'Saving'
					: !dirty
						? 'Nothing has changed yet'
						: `Save ${row.label}`}
			onclick={() => store.save(row)}
		>
			{busy ? '…' : 'SAVE'}
		</Button>
		{#if row.source === 'overlay' || row.source === 'unapplied'}
			<Button
				testid="reset-{row.key}"
				disabled={busy}
				title="Put the value in configuration.yaml back"
				aria-label="Reset {row.label} to the value in configuration.yaml"
				onclick={() => store.reset(row)}
			>
				RESET
			</Button>
		{/if}
	{/snippet}
	{#snippet note()}
			{#if row.note}<p data-testid="note-{row.key}">{row.note}</p>{/if}
			{#if row.unapplied_reason}
				<p class="bad" data-testid="unapplied-{row.key}" role="alert">{row.unapplied_reason}</p>
			{:else if locked}
				<p data-testid="package-{row.key}">
					Set by packages/{row.package}.yaml — edit that file to change it.
				</p>
			{/if}
			{#if store.fieldError[row.key]}
				<p class="bad" data-testid="error-{row.key}" role="alert">{store.fieldError[row.key]}</p>
			{/if}
	{/snippet}
</SettingRowUi>

<style>
	/* The grid is SettingRow's (M107); the key's face and a bad note's colour are this row's. */
	:global(.setting .what code) {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
	}
	:global(.setting .note p.bad) {
		color: var(--jv-danger-text);
	}
</style>