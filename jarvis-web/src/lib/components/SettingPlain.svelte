<!--
@component
One setting in plain words: what it is called, one line on why anybody would
change it, the control, and SAVE — lit only once something changed. The key,
the source and the server's note are not here; they are on the raw row of the
same setting behind EVERYTHING, for the person who wants them.

```svelte
<SettingPlain {store} row={store.row('voice.wake_word')} label="Wake word" why="What you say to get its attention." />
```
-->
<script lang="ts">
	import type { SettingRow } from '$lib/jarvisClient';
	import type { SettingsStore } from '$lib/settingsStore.svelte';
	import { Button, SettingRow as SettingRowUi } from '$lib/ui';
	import SettingControl from './SettingControl.svelte';

	interface Props {
		store: SettingsStore;
		row: SettingRow;
		label: string;
		why: string;
	}
	let { store, row, label, why }: Props = $props();

	const locked = $derived(row.source === 'package');
	const busy = $derived(store.busyKey === row.key);
	const dirty = $derived(store.isDirty(row));
</script>

<SettingRowUi
	{label}
	{why}
	testid="plain-{row.key}"
	live={dirty}
	noted={Boolean(locked || row.unapplied_reason || store.fieldError[row.key])}
>
	<SettingControl {store} {row} testid="plain-input-{row.key}" disabled={locked} />
	{#snippet acts()}
		<!-- SAVE is lit only once something changed: the accent is spent on the
		     one thing on this page that is about to happen. -->
		<Button
			variant={dirty ? 'primary' : 'ghost'}
			testid="plain-save-{row.key}"
			disabled={locked || busy || !dirty}
			title={locked
				? 'This setting is fixed in configuration.yaml'
				: busy
					? 'Saving'
					: !dirty
						? 'Nothing has changed yet'
						: `Save ${label}`}
			onclick={() => store.save(row)}
		>
			{busy ? '…' : 'SAVE'}
		</Button>
		{#if row.source === 'overlay' || row.source === 'unapplied'}
			<Button
				testid="plain-reset-{row.key}"
				disabled={busy}
				title="Put the value in configuration.yaml back"
				aria-label="Reset {label} to the value in configuration.yaml"
				onclick={() => store.reset(row)}
			>
				RESET
			</Button>
		{/if}
	{/snippet}
	{#snippet note()}
			{#if locked}
				<p data-testid="plain-package-{row.key}">
					Set by packages/{row.package}.yaml — edit that file to change it.
				</p>
			{:else if row.unapplied_reason}
				<p class="bad" data-testid="plain-unapplied-{row.key}" role="alert">{row.unapplied_reason}</p>
			{/if}
			{#if store.fieldError[row.key]}
				<p class="bad" data-testid="plain-error-{row.key}" role="alert">{store.fieldError[row.key]}</p>
			{/if}
	{/snippet}
</SettingRowUi>

<style>
	/* The row's grid, its label and its actions are SettingRow's (M107); only
	   the colour of a bad note is this component's. */
	:global(.setting .note p.bad) {
		color: var(--jv-danger-text);
	}
</style>