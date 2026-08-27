<script lang="ts">
	// The power-on sequence: scan line -> the instrument assembles, bezel ->
	// blades -> coil -> level -> core -> the JARVIS wordmark -> three system
	// checks -> dissolve into the app. ~1.2 s, once per browser session, and
	// never at all when the user has asked for reduced motion.
	//
	// One rAF loop, one source of truth (`$lib/boot`). The overlay is
	// `pointer-events: none` throughout, so the app underneath is live from the
	// first frame — this is a curtain being pulled back, not a loading gate.
	//
	// Re-staged for Reactor II (M49): the four rings that used to materialise
	// outward are the instrument's own layers now, drawn by the same `Reactor`
	// the voice screen shows, so what the boot assembles is what the screen
	// settles into.
	import { onMount } from 'svelte';
	import * as boot from '$lib/boot';
	import { prefersReducedMotion } from '$lib/motion';
	import { Reactor } from '$lib/ui';

	let { onDone }: { onDone?: () => void } = $props();

	let playing = $state(false);
	let frame = $state<boot.BootFrame>(boot.frameAt(0));

	/** The instrument's layers, from the timeline's four rings and its core. */
	const reveal = $derived({
		bezel: frame.ringAlpha[0] ?? 0,
		blades: frame.ringAlpha[1] ?? 0,
		coil: frame.ringAlpha[2] ?? 0,
		level: frame.ringAlpha[3] ?? 0,
		core: frame.coreAlpha
	});

	function finish(): void {
		playing = false;
		onDone?.();
	}

	onMount(() => {
		const storage = typeof sessionStorage === 'undefined' ? null : sessionStorage;
		if (
			boot.shouldSkipBoot({
				reducedMotion: prefersReducedMotion(),
				alreadyPlayed: boot.bootAlreadyPlayed(storage)
			})
		) {
			boot.markBootPlayed(storage);
			return;
		}

		boot.markBootPlayed(storage);
		playing = true;
		const started = performance.now();
		let raf = 0;
		const step = (now: number) => {
			const t = now - started;
			frame = boot.frameAt(t);
			if (t >= boot.TOTAL_MS) {
				finish();
				return;
			}
			raf = requestAnimationFrame(step);
		};
		raf = requestAnimationFrame(step);

		// Escape, click or any key jumps to the end state — which is the same
		// frame the timeline reaches on its own, so skipping is invisible.
		const skip = () => {
			if (!playing) return;
			cancelAnimationFrame(raf);
			frame = boot.endFrame();
			finish();
		};
		window.addEventListener('keydown', skip, { once: true });
		window.addEventListener('pointerdown', skip, { once: true });

		return () => {
			cancelAnimationFrame(raf);
			window.removeEventListener('keydown', skip);
			window.removeEventListener('pointerdown', skip);
		};
	});
</script>

{#if playing}
	<div
		class="boot"
		data-testid="boot"
		data-stage={frame.stage}
		aria-hidden="true"
		style="opacity:{frame.chromeAlpha}"
	>
		<i class="scan" style="top:{frame.scanY * 100}%; opacity:{frame.scanAlpha}"></i>

		<div class="stage">
			<i class="flare" style="opacity:{frame.flareAlpha}"></i>
			<div class="instrument" style="transform:scale({0.92 + frame.coreScale * 0.08})">
				<Reactor size={360} fluid level={0.38} state="idle" {reveal} testid="boot-reactor" label="" />
			</div>

			<div class="word" style="letter-spacing:{frame.letterSpacing}em">
				{#each boot.WORDMARK.split('') as letter, i (i)}
					<span style="opacity:{frame.letterAlpha[i]}; filter:blur({frame.letterBlur[i]}px)">{letter}</span>
				{/each}
			</div>

			<div class="checks" data-testid="boot-checks">
				{#each frame.checkLines as line, i (i)}
					<span>{line}</span>
				{/each}
			</div>
		</div>
	</div>
{/if}

<style>
	.boot {
		position: fixed;
		inset: 0;
		z-index: 80;
		/* Never blocks the app underneath: the sequence is decoration, not a gate. */
		pointer-events: none;
		display: grid;
		place-items: center;
		background: var(--jv-bg);
		overflow: hidden;
	}
	.scan {
		position: absolute;
		left: 0;
		right: 0;
		height: 1px;
		background: linear-gradient(90deg, transparent, var(--jv-accent), transparent);
		box-shadow: var(--jv-glow-md);
	}
	.stage {
		position: relative;
		display: grid;
		place-items: center;
		width: min(52vmin, var(--jv-measure-boot));
		height: min(52vmin, var(--jv-measure-boot));
	}
	.instrument {
		width: 100%;
		height: 100%;
		transform-origin: 50% 50%;
	}
	.flare {
		position: absolute;
		width: 100%;
		height: 100%;
		border-radius: 50%;
		background: radial-gradient(circle, color-mix(in srgb, var(--jv-accent) 40%, transparent) 0%, transparent 48%);
	}
	.word {
		position: absolute;
		bottom: calc(-1 * var(--jv-space-7));
		display: flex;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-xl);
		color: var(--jv-text-bright);
	}
	.checks {
		position: absolute;
		bottom: calc(-1 * var(--jv-space-7) * 2.5);
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-1);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		min-width: calc(var(--jv-space-7) * 4);
	}
	.checks span:not(:empty)::before {
		content: '› ';
		color: var(--jv-accent);
	}
</style>
