<!--
@component
A big number that counts up to its value — a dashboard's figure, a task's
percentage — set in the display face with a mono unit. It counts from where it
was to where it is, over `--jv-dur-enter`, so a live value tweens rather than
snaps; under reduced motion it simply is the value.

```svelte
<Figure value={31.4} unit="tok/s" decimals={1} live />
```
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { prefersReducedMotion } from '$lib/motion';
	import { tokenMs } from '$lib/tokens';

	interface Props {
		value: number | null;
		unit?: string;
		decimals?: number;
		/** In the accent: this number is happening now. */
		live?: boolean;
		/** Smaller, for a card that is not the hero. */
		small?: boolean;
		testid?: string;
	}
	let { value, unit = '', decimals = 0, live = false, small = false, testid = '' }: Props = $props();

	let shown = $state(0);
	let raf = 0;
	let mounted = false;

	function tween(from: number, to: number): void {
		if (raf) cancelAnimationFrame(raf);
		if (prefersReducedMotion() || !mounted) {
			shown = to;
			return;
		}
		const ms = tokenMs('--jv-dur-enter');
		const t0 = performance.now();
		const tick = (t: number) => {
			const k = Math.min(1, (t - t0) / ms);
			const e = 1 - Math.pow(1 - k, 3);
			shown = from + (to - from) * e;
			if (k < 1) raf = requestAnimationFrame(tick);
			else raf = 0;
		};
		raf = requestAnimationFrame(tick);
	}

	$effect(() => {
		const target = value ?? 0;
		tween(shown, target);
	});

	onMount(() => {
		mounted = true;
		return () => {
			if (raf) cancelAnimationFrame(raf);
		};
	});

	const text = $derived(value === null ? '—' : shown.toFixed(decimals));
</script>

<span class="figure" class:live class:small data-testid={testid || undefined} data-value={value ?? ''}>
	{text}{#if unit && value !== null}<small>{unit}</small>{/if}
</span>

<style>
	.figure {
		display: inline-flex;
		align-items: baseline;
		gap: var(--jv-space-1);
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-display);
		line-height: 1;
		color: var(--jv-text-bright);
		font-variant-numeric: tabular-nums;
	}
	.figure.small {
		font-size: var(--jv-fs-2xl);
	}
	.figure.live {
		color: var(--jv-accent);
	}
	small {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
</style>
