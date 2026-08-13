<script lang="ts">
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

	// A light turning on flashes its own pill, so a change you caused (or that
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
		class="pill"
		class:on
		data-testid="state-{entityId}"
		aria-label="{name} state">{state.state}</span
	>

	<div class="ctl">
		{#if domain === 'light' || domain === 'switch' || domain === 'fan' || domain === 'siren' || domain === 'input_boolean'}
			<button
				type="button"
				class="btn"
				class:on
				disabled={unavailable}
				data-testid="toggle-{entityId}"
				aria-label="{on ? 'Turn off' : 'Turn on'} {name}"
				onclick={() => call(on ? 'turn_off' : 'turn_on')}
			>
				{on ? 'TURN OFF' : 'TURN ON'}
			</button>
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
			<button type="button" class="btn" data-testid="open-{entityId}" aria-label="Open {name}" onclick={() => call('open_cover')}>OPEN</button>
			<button type="button" class="btn ghost" aria-label="Stop {name}" onclick={() => call('stop_cover')}>STOP</button>
			<button type="button" class="btn" data-testid="close-{entityId}" aria-label="Close {name}" onclick={() => call('close_cover')}>
				CLOSE
			</button>
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
				<span class="muted">now {attrs.current_temperature}°</span>
			{/if}
			<label class="slider">
				<span class="slabel">SET</span>
				<input
					type="number"
					step="0.5"
					style="width:5rem"
					value={num(attrs.temperature, 20)}
					data-testid="setpoint-{entityId}"
					aria-label="{name} target temperature"
					onchange={(e) => call('set_temperature', { temperature: num(target(e).value, 20) })}
				/>
			</label>
			<select
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
			<button
				type="button"
				class="btn ghost"
				data-testid="prev-{entityId}"
				aria-label="Previous track on {name}"
				onclick={() => call('media_previous_track')}
			>
				<span aria-hidden="true">‹‹</span>
			</button>
			<button type="button" class="btn" data-testid="play-{entityId}" aria-label="Play {name}" onclick={() => call('media_play')}>PLAY</button>
			<button type="button" class="btn" data-testid="pause-{entityId}" aria-label="Pause {name}" onclick={() => call('media_pause')}>
				PAUSE
			</button>
			<button
				type="button"
				class="btn ghost"
				data-testid="next-{entityId}"
				aria-label="Next track on {name}"
				onclick={() => call('media_next_track')}
			>
				<span aria-hidden="true">››</span>
			</button>
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
			<button type="button" class="btn" data-testid="lock-{entityId}" aria-label="Lock {name}" onclick={() => call('lock')}>LOCK</button>
			<button type="button" class="btn ghost" data-testid="unlock-{entityId}" aria-label="Unlock {name}" onclick={() => call('unlock')}>
				UNLOCK
			</button>
		{:else if domain === 'button' || domain === 'scene' || domain === 'script'}
			<button
				type="button"
				class="btn"
				data-testid="press-{entityId}"
				aria-label="Run {name}"
				onclick={() => call(domain === 'button' ? 'press' : 'turn_on')}
			>
				RUN
			</button>
		{:else if domain === 'select' || domain === 'input_select'}
			<select
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
				style="width:6rem"
				min={attrs.min}
				max={attrs.max}
				step={attrs.step ?? 1}
				value={num(state.state)}
				data-testid="value-{entityId}"
				aria-label="{name} value"
				onchange={(e) => call('set_value', { value: num(target(e).value) })}
			/>
		{:else if domain === 'vacuum'}
			<button type="button" class="btn" aria-label="Start {name}" onclick={() => call('start')}>START</button>
			<button type="button" class="btn ghost" aria-label="Send {name} to its dock" onclick={() => call('return_to_base')}>DOCK</button>
		{:else if attrs.unit_of_measurement}
			<span class="muted">{attrs.unit_of_measurement}</span>
		{/if}
	</div>
</div>

<style>
	.ctl {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		flex-wrap: wrap;
	}
	.slider {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}
	/*
	 * Tokens, not `--chrome` / `--dim`.
	 *
	 * Those two are declared inside `.hud` in the HUD's own page, and this row is
	 * only ever drawn inside `.console` — so both lookups fell through to nothing
	 * and every slider label lost its font AND its colour, inheriting the body
	 * face at the row's text colour. It looked like a design decision.
	 */
	.slabel {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		color: var(--jv-text-dim);
		opacity: 0.7;
	}
</style>
