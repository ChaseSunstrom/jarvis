<!--
@component
One entity on the dashboard: its state, large, in the display face; when it
last changed, in mono under it; and the one control it can take from where
it is — TURN OFF for a light that is on, UNLOCK for a locked lock — the same
service the HOUSE › Devices row would call. Nothing else: a tile is read
across a room.

Honest when the entity is not there. A tile pointed at an entity this Jarvis
has never seen says so and how to fix it, rather than showing `—` forever.
```svelte
<EntityTile entityId="light.hall_lamp" state={states.get('light.hall_lamp')} live onswitch={call} />
```
-->
<script lang="ts">
	import { Button } from '$lib/ui';
	import { friendlyName, isOn, type EntityState } from '$lib/jarvisClient';
	import { ago, secondsSince, stateText, switchFor } from './widgets';

	interface Props {
		entityId: string;
		state: EntityState | undefined;
		/** This is the value happening now: the accent, not the dim. */
		live?: boolean;
		/** Waiting for the backend to answer a press. */
		busy?: boolean;
		/** Called with the service to run against this entity. */
		onswitch?: (service: string) => void;
		/** What the last press said, if it failed. */
		error?: string;
		/** For the "changed … ago" line; a test pins the clock. */
		now?: number;
	}
	let { entityId, state, live = false, busy = false, onswitch, error = '', now }: Props = $props();

	const on = $derived(isOn(state));
	const control = $derived(state ? switchFor(state) : null);
	const since = $derived(secondsSince(state?.last_changed, now));
	const unavailable = $derived(state?.state === 'unavailable');
	const domain = $derived(entityId.split('.')[0] ?? '');
	// A lock's test ids follow the state (`unlock-…` while locked), as the
	// Devices rows do, so a spec presses what a person sees.
	const testid = $derived(
		control ? (domain === 'lock' ? `${control.service}-${entityId}` : `toggle-${entityId}`) : ''
	);
</script>

{#if !state}
	<p class="why" data-testid="tile-why">
		No entity called <code>{entityId}</code> on this Jarvis. Add the device, or point this tile at one of yours.
	</p>
{:else}
	<div class="tile" data-testid="tile-{entityId}">
		<span class="name">{friendlyName(state)}</span>
		<span class="value" class:live class:on class:unavailable data-testid="tile-state-{entityId}">
			{stateText(state)}
		</span>
		<span class="since">
			{since === null ? entityId : `changed ${ago(since)}`}
		</span>
		{#if control}
			<div class="ctl">
				<Button
					pressed={on}
					disabled={busy || unavailable}
					{testid}
					aria-label="{control.label.toLowerCase()} {friendlyName(state)}"
					onclick={() => onswitch?.(control.service)}
				>
					{control.label}
				</Button>
			</div>
		{/if}
		{#if error}<p class="err" role="alert" data-testid="tile-error">{error}</p>{/if}
	</div>
{/if}

<style>
	.tile {
		display: grid;
		grid-template-rows: auto 1fr auto auto;
		gap: var(--jv-space-1);
		min-height: 0;
		height: 100%;
	}
	.name {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	/* The state, as the tile's figure: the display face, large, the dim of a
	   value at rest — the accent only on the hero's, and only when it is on. */
	.value {
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-2xl);
		line-height: 1;
		letter-spacing: var(--jv-track-snug);
		color: var(--jv-text-dim);
		font-variant-numeric: tabular-nums;
		text-transform: capitalize;
		overflow-wrap: anywhere;
		transition: color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.value.on {
		color: var(--jv-text-bright);
	}
	.value.live.on {
		color: var(--jv-accent);
	}
	.value.unavailable {
		color: var(--jv-danger-text);
	}
	.since {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
	}
	.ctl {
		display: flex;
		gap: var(--jv-space-2);
	}
	.why,
	.err {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.err {
		color: var(--jv-danger-text);
	}
	code {
		font-family: var(--jv-font-chrome);
		color: var(--jv-text-dim);
	}
</style>
