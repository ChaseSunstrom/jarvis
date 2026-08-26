<script lang="ts">
	/**
	 * SETTINGS › Assistant: the models, and how it answers.
	 *
	 * The MODELS panel first, because "which model" is the question this
	 * section exists for and the one the old page answered with an alias. Then
	 * the three rows a person changes — temperature, name, language — in plain
	 * words. Everything else jarvis-core files under Assistant is behind
	 * EVERYTHING, as the server describes it.
	 */
	import { onMount } from 'svelte';
	import Models from '$lib/components/Models.svelte';
	import SettingPlain from '$lib/components/SettingPlain.svelte';
	import SettingRaw from '$lib/components/SettingRaw.svelte';
	import SettingsFold from '$lib/components/SettingsFold.svelte';
	import { relayUrl } from '$lib/connection';
	import { SectionLink } from '$lib/sectionLink.svelte';
	import { SettingsStore } from '$lib/settingsStore.svelte';
	import { Panel, Pill, ScreenState, SkeletonRows } from '$lib/ui';
	import { featuredOf, sectionOfGroup } from './settingsPlan';

	const store = new SettingsStore();
	const link = new SectionLink(async (conn) => {
		await store.load(conn.client);
	});

	onMount(() => link.mount());

	const featured = featuredOf('assistant');
	/** The groups this section owns: Assistant, and any group the plan has never heard of. */
	const raw = $derived(store.rows.filter((row) => sectionOfGroup(row.group) === 'assistant'));
	let everything = $state(false);
</script>

<div class="stack">
	<p class="lede" data-testid="assistant-screen">
		link {link.status} · relay <code>{typeof location === 'undefined' ? '' : relayUrl()}</code>
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
			<span>
				Saved, but {store.restartNeeded.length === 1 ? 'this setting needs' : 'these settings need'} a
				restart of jarvis-core to take effect: {store.restartNeeded.join(', ')}.
			</span>
		</p>
	{/if}

	<Models conn={link.conn} status={link.status} onsaved={(result) => store.absorb(result)} />

	{#if store.supported}
		{#if !store.loaded && link.status !== 'closed' && link.status !== 'error'}
			<Panel title="Assistant" meta="…">
				{#snippet children()}<SkeletonRows rows={3} label="Loading settings" />{/snippet}
			</Panel>
		{:else}
			<Panel title="Assistant" meta={`${featured.length}`} testid="group-assistant">
				{#snippet children()}
					{#each featured as item (item.key)}
						{@const row = store.row(item.key)}
						{#if row}
							<SettingPlain {store} {row} label={item.label} why={item.why} />
						{/if}
					{/each}
				{/snippet}
			</Panel>

			<!-- The rest, exactly as the server describes it: key, source, note. -->
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
	/* The section's one-line status, in the body face with only the address in mono. */
	.lede {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.lede code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text);
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
</style>
