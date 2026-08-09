<script lang="ts">
	import { domainOf, isOn, type EntityState } from '$lib/jarvisClient';

	interface Props {
		state: EntityState;
		name: string;
		/** Issue a service call in this entity's domain against this entity. */
		call: (service: string, data?: Record<string, any>) => void;
	}
	let { state, name, call }: Props = $props();

	let entityId = $derived(state.entity_id);
	let domain = $derived(domainOf(entityId));
	let on = $derived(isOn(state));
	let attrs = $derived(state.attributes ?? {});
	let unavailable = $derived(state.state === 'unavailable');

	function num(value: unknown, fallback = 0): number {
		const n = Number(value);
		return Number.isFinite(n) ? n : fallback;
	}
	function target(e: Event): HTMLInputElement {
		return e.currentTarget as HTMLInputElement;
	}
</script>

<div class="row" data-testid="entity-{entityId}">
	<span class="name">
		<b>{name}</b>
		<span class="eid">{entityId}</span>
	</span>

	<span class="pill" class:on data-testid="state-{entityId}">
		{state.state}
	</span>

	<div class="ctl">
		{#if domain === 'light' || domain === 'switch' || domain === 'fan' || domain === 'siren' || domain === 'input_boolean'}
			<button
				class="btn"
				class:on
				disabled={unavailable}
				data-testid="toggle-{entityId}"
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
						onchange={(e) => call('turn_on', { percentage: num(target(e).value) })}
					/>
				</label>
			{/if}
		{:else if domain === 'cover'}
			<button class="btn" data-testid="open-{entityId}" onclick={() => call('open_cover')}>OPEN</button>
			<button class="btn ghost" onclick={() => call('stop_cover')}>STOP</button>
			<button class="btn" data-testid="close-{entityId}" onclick={() => call('close_cover')}>
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
					onchange={(e) => call('set_temperature', { temperature: num(target(e).value, 20) })}
				/>
			</label>
			<select
				data-testid="hvac-{entityId}"
				value={state.state}
				onchange={(e) => call('set_hvac_mode', { hvac_mode: (e.currentTarget as HTMLSelectElement).value })}
			>
				{#each (attrs.hvac_modes ?? ['off', 'heat', 'cool', 'auto']) as mode (mode)}
					<option value={mode}>{mode}</option>
				{/each}
			</select>
		{:else if domain === 'media_player'}
			<button class="btn ghost" data-testid="prev-{entityId}" onclick={() => call('media_previous_track')}>
				‹‹
			</button>
			<button class="btn" data-testid="play-{entityId}" onclick={() => call('media_play')}>PLAY</button>
			<button class="btn" data-testid="pause-{entityId}" onclick={() => call('media_pause')}>
				PAUSE
			</button>
			<button class="btn ghost" data-testid="next-{entityId}" onclick={() => call('media_next_track')}>
				››
			</button>
			<label class="slider">
				<span class="slabel">VOL</span>
				<input
					type="range"
					min="0"
					max="100"
					value={Math.round(num(attrs.volume_level) * 100)}
					data-testid="volume-{entityId}"
					onchange={(e) => call('volume_set', { volume_level: num(target(e).value) / 100 })}
				/>
			</label>
		{:else if domain === 'lock'}
			<button class="btn" data-testid="lock-{entityId}" onclick={() => call('lock')}>LOCK</button>
			<button class="btn ghost" data-testid="unlock-{entityId}" onclick={() => call('unlock')}>
				UNLOCK
			</button>
		{:else if domain === 'button' || domain === 'scene' || domain === 'script'}
			<button
				class="btn"
				data-testid="press-{entityId}"
				onclick={() => call(domain === 'button' ? 'press' : 'turn_on')}
			>
				RUN
			</button>
		{:else if domain === 'select' || domain === 'input_select'}
			<select
				data-testid="select-{entityId}"
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
				onchange={(e) => call('set_value', { value: num(target(e).value) })}
			/>
		{:else if domain === 'vacuum'}
			<button class="btn" onclick={() => call('start')}>START</button>
			<button class="btn ghost" onclick={() => call('return_to_base')}>DOCK</button>
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
	.slabel {
		font-family: var(--chrome);
		font-size: 0.52rem;
		letter-spacing: 0.16em;
		color: var(--dim);
		opacity: 0.7;
	}
</style>
