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
	import { Button } from '$lib/ui';
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

<div class="setting" data-testid="plain-{row.key}">
	<div class="what">
		<b>{label}</b>
		<span class="why">{why}</span>
	</div>
	<div class="control">
		<SettingControl {store} {row} testid="plain-input-{row.key}" disabled={locked} />
	</div>
	<div class="acts">
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
	</div>
	{#if locked}
		<p class="note" data-testid="plain-package-{row.key}">
			Set by packages/{row.package}.yaml — edit that file to change it.
		</p>
	{:else if row.unapplied_reason}
		<p class="note bad" data-testid="plain-unapplied-{row.key}" role="alert">{row.unapplied_reason}</p>
	{/if}
	{#if store.fieldError[row.key]}
		<p class="note bad" data-testid="plain-error-{row.key}" role="alert">{store.fieldError[row.key]}</p>
	{/if}
</div>

<style>
	/* One setting: what it is, the control, the actions — on a hairline. */
	.setting {
		display: grid;
		grid-template-columns: minmax(12rem, 1fr) minmax(10rem, 1.4fr) auto;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
		padding: var(--jv-space-3) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.setting:last-child {
		border-bottom: 0;
	}
	.what {
		display: grid;
		gap: var(--jv-space-1);
		min-width: 0;
	}
	.what b {
		font-weight: var(--jv-weight-label);
		color: var(--jv-text-bright);
	}
	.why {
		font-size: var(--jv-fs-xs);
		line-height: 1.5;
		color: var(--jv-text-dim);
		max-width: 44ch;
	}
	.control {
		min-width: 0;
	}
	.acts {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
	}
	.note {
		grid-column: 1 / -1;
		margin: 0;
		font-size: var(--jv-fs-xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
		max-width: 80ch;
	}
	.note.bad {
		color: var(--jv-danger-text);
	}
	@media (max-width: 720px) {
		.setting {
			grid-template-columns: minmax(0, 1fr);
		}
		.acts {
			justify-content: flex-start;
		}
	}
</style>
