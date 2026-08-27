<!--
@component
Tonight, from the sky integration: when the ISS next rises over the house —
the time as the figure, in the display face — how high and where, whether it
will be lit; and the moon, its phase and how much of it. Two things a person
would otherwise look up.

Every number here was computed from cached orbital elements, and the tile says
how old they are. When nothing has been downloaded yet it says "not fetched
yet" and why, never a guessed time — a wrong pass time is worse than none.
```svelte
<SkyTonight sky={summary} live />
```
-->
<script lang="ts">
	import { clock, moonSentence, passSentence, type SkySummary } from './widgets';

	interface Props {
		/** null until the first answer comes back. */
		sky: SkySummary | null;
		live?: boolean;
	}
	let { sky, live = false }: Props = $props();

	const passWhy = $derived(sky ? passSentence(sky) : '');
	const moonWhy = $derived(sky ? moonSentence(sky) : '');
	const pass = $derived(sky?.pass ?? null);
	const moon = $derived(sky?.moon ?? null);
	const name = $derived(sky?.satellite?.replace(/\s*\(.*\)$/, '') || 'ISS');
	const age = $derived(
		pass?.tle_age_hours !== null && pass?.tle_age_hours !== undefined
			? pass.tle_age_hours < 48
				? `elements ${Math.round(pass.tle_age_hours)} h old`
				: `elements ${Math.round(pass.tle_age_hours / 24)} d old`
			: ''
	);
</script>

{#if !sky}
	<p class="why" aria-busy="true">Asking the sky…</p>
{:else}
	<div class="sky" data-testid="sky">
		<section class="pass" data-testid="sky-pass" data-state={pass?.state ?? 'unconfigured'}>
			<h4>{name} next rises</h4>
			{#if passWhy}
				<p class="why">{passWhy}</p>
			{:else if pass}
				<span class="figure" class:live>{clock(pass.state)}</span>
				<span class="detail">
					{#if pass.direction}in the {pass.direction}{/if}
					{#if pass.max_alt !== null} · up to {Math.round(pass.max_alt)}°{/if}
					{#if pass.visible === true}
						· visible
					{:else if pass.visible === false && pass.next_visible}
						· visible {clock(pass.next_visible)}
					{:else if pass.visible === false}
						· not visible
					{/if}
				</span>
				{#if age}<span class="age">{age}</span>{/if}
			{/if}
		</section>
		<section class="moon" data-testid="sky-moon" data-state={moon?.state ?? 'unconfigured'}>
			<h4>The moon</h4>
			{#if moonWhy}
				<p class="why">{moonWhy}</p>
			{:else if moon}
				<span class="phase">{moon.state}</span>
				<span class="detail">
					{#if moon.illumination !== null}{Math.round(moon.illumination)}% lit{/if}
					{#if moon.state === 'full moon' && moon.next_new}
						· new {clock(moon.next_new)}
					{:else if moon.next_full}
						· full {clock(moon.next_full)}
					{/if}
				</span>
			{/if}
		</section>
	</div>
{/if}

<style>
	.sky {
		display: grid;
		gap: var(--jv-space-3);
		min-height: 0;
	}
	section {
		display: grid;
		gap: var(--jv-space-1);
	}
	.moon {
		padding-top: var(--jv-space-2);
		border-top: 1px solid var(--jv-line-hair);
	}
	h4 {
		margin: 0;
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	/* The rise time is the tile's figure: the display face, tabular, the
	   accent only when this widget is the hero. */
	.figure {
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-2xl);
		line-height: 1;
		letter-spacing: var(--jv-track-snug);
		color: var(--jv-text-bright);
		font-variant-numeric: tabular-nums;
	}
	.figure.live {
		color: var(--jv-accent);
	}
	.phase {
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-lg);
		color: var(--jv-text-bright);
		text-transform: capitalize;
	}
	.detail {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		font-variant-numeric: tabular-nums;
	}
	.age,
	.why {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
	}
	.why {
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-xs);
		letter-spacing: normal;
	}
</style>
