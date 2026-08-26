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
	import { Button, Pill } from '$lib/ui';
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

<div class="setting" data-testid="setting-{row.key}">
	<div class="what">
		<b>{row.label}</b>
		<code>{row.key}</code>
	</div>
	<div class="control">
		<SettingControl {store} {row} testid="input-{row.key}" disabled={locked} />
	</div>
	<div class="acts">
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
	</div>
	{#if row.note}<p class="note" data-testid="note-{row.key}">{row.note}</p>{/if}
	{#if row.unapplied_reason}
		<p class="note bad" data-testid="unapplied-{row.key}" role="alert">{row.unapplied_reason}</p>
	{:else if locked}
		<p class="note" data-testid="package-{row.key}">
			Set by packages/{row.package}.yaml — edit that file to change it.
		</p>
	{/if}
	{#if store.fieldError[row.key]}
		<p class="note bad" data-testid="error-{row.key}" role="alert">{store.fieldError[row.key]}</p>
	{/if}
</div>

<style>
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
	.what code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
		overflow-wrap: anywhere;
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
