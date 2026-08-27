<script lang="ts">
	/**
	 * SETTINGS › House: where the house is.
	 *
	 * Time zone and units are what a person changes; the rooms are managed on
	 * HOUSE › Areas and this section says so rather than growing a second
	 * editor. Coordinates, currency, country, elevation and the log level are
	 * behind EVERYTHING, as the server describes them.
	 */
	import { onMount } from 'svelte';
	import SettingPlain from '$lib/components/SettingPlain.svelte';
	import SettingRaw from '$lib/components/SettingRaw.svelte';
	import SettingsFold from '$lib/components/SettingsFold.svelte';
	import { SectionLink } from '$lib/sectionLink.svelte';
	import { SettingsStore } from '$lib/settingsStore.svelte';
	import { Panel, Pill, ScreenState, SkeletonRows, SettingRow } from '$lib/ui';
	import { featuredOf, sectionOfGroup } from './settingsPlan';

	const store = new SettingsStore();
	const link = new SectionLink(async (conn) => {
		await store.load(conn.client);
	});
	onMount(() => link.mount());

	const featured = featuredOf('house');
	const raw = $derived(store.rows.filter((row) => sectionOfGroup(row.group) === 'house'));
	const zone = $derived(store.row('jarvis.time_zone'));
	let everything = $state(false);
</script>

<div class="stack">
	<p class="lede" data-testid="settings-house-lede">
		{zone?.value ? `clock set to ${zone.value}` : 'time zone not set'} · link {link.status}
	</p>

	<ScreenState
		status={link.screen}
		errorTitle="This page hit an error"
		errorDetail={link.err}
		onretry={() => link.connect()}
		onreconnect={() => link.connect()}
		busy={link.redialling}
		errorTestid="error"
	/>

	{#if store.restartNeeded.length}
		<p class="line" data-testid="restart-needed">
			<Pill tone="warn">needs a restart</Pill>
			<span>Saved, but these need a restart of jarvis-core to take effect: {store.restartNeeded.join(', ')}.</span>
		</p>
	{/if}

	{#if store.supported}
		{#if !store.loaded && link.status !== 'closed' && link.status !== 'error'}
			<Panel title="House" meta="…">
				{#snippet children()}<SkeletonRows rows={3} label="Loading settings" />{/snippet}
			</Panel>
		{:else}
			<Panel title="House" meta={`${featured.length + 1}`} testid="group-house">
				{#snippet children()}
					{#each featured as item (item.key)}
						{@const row = store.row(item.key)}
						{#if row}
							<SettingPlain {store} {row} label={item.label} why={item.why} />
						{/if}
					{/each}
					<!-- The rooms are not a setting; they are a registry with its own
					     section. A link, so this page does not grow a second editor. -->
					<SettingRow label="Rooms" why="The areas voice commands resolve against, and what is in each." testid="plain-areas">
						<span class="value">managed on HOUSE</span>
						{#snippet acts()}
							<a class="go" href="/house/areas" data-testid="areas-link">OPEN AREAS →</a>
						{/snippet}
					</SettingRow>
				{/snippet}
			</Panel>

			<SettingsFold
				title="Everything"
				meta={`${raw.length} setting${raw.length === 1 ? '' : 's'} · as the server lists them`}
				bind:open={everything}
				testid="everything"
				summaryTestid="everything-summary"
			>
				{#snippet children()}
					{#each raw as row (row.key)}
						<SettingRaw {store} {row} />
					{/each}
				{/snippet}
			</SettingsFold>
		{/if}
	{/if}
</div>

<style>
	.stack {
		display: grid;
		gap: var(--jv-space-4);
	}
	.lede {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.line {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	/* The row's grid is SettingRow's (M107). */
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
	.value {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
	}
	.acts {
		display: flex;
		justify-content: flex-end;
	}
	/* A link drawn as the console's ghost button: uppercase Barlow on a hairline. */
	.go {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		text-decoration: none;
		color: var(--jv-text);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-3);
		transition: border-color var(--jv-dur-fast) var(--jv-ease-out), color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.go:hover {
		color: var(--jv-text-bright);
		border-color: var(--jv-line);
	}
	@media (max-width: 720px) {
		.acts {
			justify-content: flex-start;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.go {
			transition: none;
		}
	}
</style>
