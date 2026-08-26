<!--
@component
The newest sensor readings, room by room: hairline rows under a room name, the
value in mono with tabular numerals so a column of them lines up, the age in
the faint. The newest reading on the page carries the accent when this widget
is the hero — the one value happening now.

Empty is a sentence, not a blank: what would be here and how it gets here,
which differs by whether the sensors integration is set up at all.
```svelte
<Readings groups={groupReadings(rows)} configured live />
```
-->
<script lang="ts">
	import { ago, readingText, type ReadingGroup } from './widgets';

	interface Props {
		groups: ReadingGroup[];
		/** Whether the sensors integration is there to bring new readings in. */
		configured?: boolean;
		/** The room this widget was limited to, for its empty sentence. */
		area?: string;
		live?: boolean;
	}
	let { groups, configured = true, area = '', live = false }: Props = $props();

	const empty = $derived(!groups.some((group) => group.readings.length));
	/** The single newest reading, which is the one the accent goes to. */
	const newest = $derived(
		[...groups.flatMap((group) => group.readings)]
			.filter((row) => row.available)
			.sort((a, b) => a.age_s - b.age_s)[0]?.entity_id ?? ''
	);
</script>

{#if empty}
	<p class="why" data-testid="readings-empty">
		{#if area}
			No readings in {area}. A sensor placed in that room appears here as it reports.
		{:else if configured}
			No readings yet. Sensors appear here as they report — over MQTT discovery, the sensors
			webhook, or any integration with a sensor.
		{:else}
			No readings yet. Add <code>sensors:</code> to configuration.yaml, or any integration
			with a sensor, and its readings appear here room by room.
		{/if}
	</p>
{:else}
	<div class="rooms" data-testid="readings">
		{#each groups as group (group.area)}
			<section class="room" data-testid="readings-room" aria-label={group.area}>
				<h4>{group.area}</h4>
				{#each group.readings as reading (reading.entity_id)}
					<div
						class="reading"
						class:dead={!reading.available}
						class:live={live && reading.entity_id === newest}
						data-testid="reading-{reading.entity_id}"
					>
						<span class="name">{reading.name}</span>
						<span class="value">{readingText(reading)}</span>
						<span class="age">{reading.available ? ago(reading.age_s) : 'no reading'}</span>
					</div>
				{/each}
			</section>
		{/each}
	</div>
{/if}

<style>
	.rooms {
		display: grid;
		gap: var(--jv-space-3);
		min-height: 0;
		overflow: auto;
	}
	h4 {
		margin: 0 0 var(--jv-space-1);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	/* One reading per hairline row: the name, the number, the age. */
	.reading {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto auto;
		gap: var(--jv-space-3);
		align-items: baseline;
		padding: var(--jv-space-1) 0;
		border-top: 1px solid var(--jv-line-hair);
	}
	.name {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.value {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.reading.live .value {
		color: var(--jv-accent);
	}
	.reading.dead .value {
		color: var(--jv-danger-text);
	}
	.age {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
		white-space: nowrap;
	}
	.why {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	code {
		font-family: var(--jv-font-chrome);
		color: var(--jv-text-dim);
	}
</style>
