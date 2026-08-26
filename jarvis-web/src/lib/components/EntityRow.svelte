<script lang="ts">
	/**
	 * One entity as one hairline row: the name in the body face, the id in
	 * mono under it, its state as a tag, and its controls — ONE of them lit
	 * when it is on. Reactor II spends the accent on what is live now, so a
	 * row of nine lit lights is nine outlines, not nine filled buttons; the
	 * filled control on a screen is its one primary action, and it is never
	 * here.
	 */
	import { Button } from '$lib/ui';
	import { domainOf, isOn, type EntityState } from '$lib/jarvisClient';
	import { staggerStyle } from '$lib/motion';

	interface Props {
		state: EntityState;
		name: string;
		/** Issue a service call in this entity's domain against this entity. */
		call: (service: string, data?: Record<string, any>) => void;
		/** Position in its group, for the staggered entrance. */
		index?: number;
	}
	let { state, name, call, index = 0 }: Props = $props();

	let entityId = $derived(state.entity_id);
	let domain = $derived(domainOf(entityId));
	let on = $derived(isOn(state));
	let attrs = $derived(state.attributes ?? {});
	let unavailable = $derived(state.state === 'unavailable');

	// A light turning on flashes its own tag, so a change you caused (or that
	// arrived from somewhere else) is visible without hunting for it.
	//
	// Restarted imperatively rather than by toggling a class: a class the
	// element already has does not replay its animation, and the whole point is
	// that the *second* change is as visible as the first.
	//
	// Plain `let`, not `$state`: this component's prop is already called `state`,
	// and the effect below only ever needs to track the entity's state string.
	let pill: HTMLElement | null = null;
	// The last state this row rendered. Deliberately not `$state` — writing it
	// must not re-run the effect that writes it. The first run only records,
	// so a row does not pulse merely for existing.
	let seen: string | undefined;
	$effect(() => {
		const value = state.state;
		const first = seen === undefined;
		if (!first && value === seen) return;
		seen = value;
		if (first || !pill) return;
		pill.classList.remove('jv-pulse');
		void pill.offsetWidth; // force a reflow so the animation can restart
		pill.classList.add('jv-pulse');
	});

	function num(value: unknown, fallback = 0): number {
		const n = Number(value);
		return Number.isFinite(n) ? n : fallback;
	}
	function target(e: Event): HTMLInputElement {
		return e.currentTarget as HTMLInputElement;
	}
</script>

<div class="row jv-stagger" style={staggerStyle(index)} data-testid="entity-{entityId}">
	<span class="name">
		<b>{name}</b>
		<span class="eid">{entityId}</span>
	</span>

	<span
		bind:this={pill}
		class="state"
		class:on
		class:unavailable
		data-testid="state-{entityId}"
		aria-label="{name} state">{state.state}</span
	>

	<div class="ctl">
		{#if domain === 'light' || domain === 'switch' || domain === 'fan' || domain === 'siren' || domain === 'input_boolean'}
			<Button
				pressed={on}
				disabled={unavailable}
				testid="toggle-{entityId}"
				aria-label="{on ? 'Turn off' : 'Turn on'} {name}"
				onclick={() => call(on ? 'turn_off' : 'turn_on')}
			>
				{on ? 'TURN OFF' : 'TURN ON'}
			</Button>
			{#if domain === 'light'}
				<label class="slider">
					<span class="slabel">BRI</span>
					<input
						type="range"
						min="0"
						max="255"
						value={num(attrs.brightness, on ? 255 : 0)}
						data-testid="brightness-{entityId}"
						aria-label="{name} brightness"
						onchange={(e) => call('turn_on', { brightness: num(target(e).value) })}
					/>
				</label>
			{/if}
			{#if domain === 'fan' && attrs.percentage !== undefined}
				<label class="slider">
					<span class="slabel">PCT</span>
					<input
						type="range"
						min="0"
						max="100"
						value={num(attrs.percentage)}
						aria-label="{name} speed"
						onchange={(e) => call('turn_on', { percentage: num(target(e).value) })}
					/>
				</label>
			{/if}
		{:else if domain === 'cover'}
			<!-- One control where one will do (M55): a cover offers the move it can
			     make from where it is, and STOP. OPEN and CLOSE side by side were
			     two buttons for one decision. -->
			{#if on}
				<Button testid="close-{entityId}" aria-label="Close {name}" onclick={() => call('close_cover')}>CLOSE</Button>
			{:else}
				<Button testid="open-{entityId}" aria-label="Open {name}" onclick={() => call('open_cover')}>OPEN</Button>
			{/if}
			<Button aria-label="Stop {name}" onclick={() => call('stop_cover')}>STOP</Button>
			<label class="slider">
				<span class="slabel">POS</span>
				<input
					type="range"
					min="0"
					max="100"
					value={num(attrs.current_position, on ? 100 : 0)}
					data-testid="position-{entityId}"
					aria-label="{name} position"
					onchange={(e) => call('set_cover_position', { position: num(target(e).value) })}
				/>
			</label>
		{:else if domain === 'climate'}
			{#if attrs.current_temperature !== undefined}
				<span class="reading">now {attrs.current_temperature}°</span>
			{/if}
			<label class="slider">
				<span class="slabel">SET</span>
				<input
					type="number"
					step="0.5"
					class="num"
					value={num(attrs.temperature, 20)}
					data-testid="setpoint-{entityId}"
					aria-label="{name} target temperature"
					onchange={(e) => call('set_temperature', { temperature: num(target(e).value, 20) })}
				/>
			</label>
			<select
				class="sel"
				data-testid="hvac-{entityId}"
				aria-label="{name} HVAC mode"
				value={state.state}
				onchange={(e) => call('set_hvac_mode', { hvac_mode: (e.currentTarget as HTMLSelectElement).value })}
			>
				{#each (attrs.hvac_modes ?? ['off', 'heat', 'cool', 'auto']) as mode (mode)}
					<option value={mode}>{mode}</option>
				{/each}
			</select>
		{:else if domain === 'media_player'}
			<Button testid="prev-{entityId}"
				aria-label="Previous track on {name}"
				onclick={() => call('media_previous_track')}
			>
				<span aria-hidden="true">‹‹</span>
			</Button>
			<!-- Play and pause are one control (M55): the one the player can take now. -->
			{#if state.state === 'playing'}
				<Button pressed testid="pause-{entityId}" aria-label="Pause {name}" onclick={() => call('media_pause')}>PAUSE</Button>
			{:else}
				<Button testid="play-{entityId}" aria-label="Play {name}" onclick={() => call('media_play')}>PLAY</Button>
			{/if}
			<Button testid="next-{entityId}"
				aria-label="Next track on {name}"
				onclick={() => call('media_next_track')}
			>
				<span aria-hidden="true">››</span>
			</Button>
			<label class="slider">
				<span class="slabel">VOL</span>
				<input
					type="range"
					min="0"
					max="100"
					value={Math.round(num(attrs.volume_level) * 100)}
					data-testid="volume-{entityId}"
					aria-label="{name} volume"
					onchange={(e) => call('volume_set', { volume_level: num(target(e).value) / 100 })}
				/>
			</label>
		{:else if domain === 'lock'}
			<!-- One control (M55): the move the lock can make from where it is. -->
			{#if state.state === 'locked'}
				<Button testid="unlock-{entityId}" aria-label="Unlock {name}" onclick={() => call('unlock')}>UNLOCK</Button>
			{:else}
				<Button testid="lock-{entityId}" aria-label="Lock {name}" onclick={() => call('lock')}>LOCK</Button>
			{/if}
		{:else if domain === 'button' || domain === 'scene' || domain === 'script'}
			<Button testid="press-{entityId}"
				aria-label="Run {name}"
				onclick={() => call(domain === 'button' ? 'press' : 'turn_on')}
			>
				RUN
			</Button>
		{:else if domain === 'select' || domain === 'input_select'}
			<select
				class="sel"
				data-testid="select-{entityId}"
				aria-label="{name} option"
				value={state.state}
				onchange={(e) =>
					call('select_option', { option: (e.currentTarget as HTMLSelectElement).value })}
			>
				{#each (attrs.options ?? [state.state]) as option (option)}
					<option value={option}>{option}</option>
				{/each}
			</select>
		{:else if domain === 'number' || domain === 'input_number'}
			<input
				type="number"
				class="num wide"
				min={attrs.min}
				max={attrs.max}
				step={attrs.step ?? 1}
				value={num(state.state)}
				data-testid="value-{entityId}"
				aria-label="{name} value"
				onchange={(e) => call('set_value', { value: num(target(e).value) })}
			/>
		{:else if domain === 'vacuum'}
			<!-- One control (M55): a cleaning vacuum offers DOCK, an idle one START. -->
			{#if state.state === 'cleaning'}
				<Button pressed aria-label="Send {name} to its dock" onclick={() => call('return_to_base')}>DOCK</Button>
			{:else}
				<Button aria-label="Start {name}" onclick={() => call('start')}>START</Button>
			{/if}
		{:else if attrs.unit_of_measurement}
			<span class="reading">{attrs.unit_of_measurement}</span>
		{/if}
	</div>
</div>

<style>
	.row {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
		min-width: 0;
		padding: var(--jv-space-3) 0;
	}
	.name {
		flex: 1 1 12rem;
		min-width: 0;
		display: grid;
		gap: var(--jv-space-1);
	}
	.name b {
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
	}
	.eid {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
		overflow-wrap: anywhere;
	}
	/* The state, as a tag: a word on a hairline, lit when the thing is on. */
	.state {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		padding: 0 var(--jv-space-2);
		line-height: 1.7;
		white-space: nowrap;
		transition: color var(--jv-dur-fast) var(--jv-ease-out), border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.state.on {
		color: var(--jv-accent);
		border-color: color-mix(in srgb, var(--jv-accent) 40%, transparent);
	}
	.state.unavailable {
		color: var(--jv-danger-text);
	}
	.ctl {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
	}
	.slider {
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-2);
	}
	/*
	 * Tokens, not `--chrome` / `--dim`.
	 *
	 * Those two were once declared inside the voice screen's own page, and this
	 * row is only ever drawn inside the console — so both lookups fell through
	 * to nothing and every slider label lost its font AND its colour. A label
	 * for a number is data, so it keeps the mono face.
	 */
	.slabel {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		color: var(--jv-text-dim);
	}
	.reading {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
		white-space: nowrap;
	}
	input[type='range'] {
		accent-color: var(--jv-accent);
		width: calc(var(--jv-space-7) * 2.6667);
	}
	.num,
	.sel {
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-1) var(--jv-space-2);
	}
	.num {
		width: calc(var(--jv-space-7) * 1.6667);
		font-family: var(--jv-font-chrome);
		font-variant-numeric: tabular-nums;
	}
	.num.wide {
		width: calc(var(--jv-space-7) * 2);
	}
	.num:hover,
	.sel:hover {
		border-color: var(--jv-line);
	}
	@media (max-width: 640px) {
		.row {
			align-items: flex-start;
		}
		.name {
			flex: 1 1 100%;
		}
		.ctl {
			width: 100%;
		}
		input[type='range'] {
			width: calc(var(--jv-space-7) * 2);
		}
	}
</style>
