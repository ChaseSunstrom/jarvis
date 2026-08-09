<script lang="ts">
	// The power-on sequence: scan line -> reactor ignite -> rings -> JARVIS
	// wordmark -> system checks -> dissolve into the app. ~1.2 s, once per
	// browser session, and never at all when the user has asked for reduced
	// motion.
	//
	// One rAF loop, one source of truth (`$lib/boot`). The overlay is
	// `pointer-events: none` throughout, so the app underneath is live from the
	// first frame — this is a curtain being pulled back, not a loading gate.
	import { onMount } from 'svelte';
	import * as boot from '$lib/boot';
	import { prefersReducedMotion } from '$lib/motion';

	let { onDone }: { onDone?: () => void } = $props();

	let playing = $state(false);
	let frame = $state<boot.BootFrame>(boot.frameAt(0));

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
		class="jv-boot"
		data-testid="boot"
		data-stage={frame.stage}
		aria-hidden="true"
		style="opacity:{frame.chromeAlpha}"
	>
		<i
			class="jv-boot-scan"
			style="top:{frame.scanY * 100}%; opacity:{frame.scanAlpha}"
		></i>

		<div class="jv-boot-stage">
			<i class="jv-boot-flare" style="opacity:{frame.flareAlpha}"></i>
			{#each frame.ringReveal as reveal, i (i)}
				<i
					class="jv-boot-ring"
					data-ring={i}
					style="transform:scale({reveal}); opacity:{frame.ringAlpha[i]}"
				></i>
			{/each}
			<i
				class="jv-boot-core"
				style="transform:scale({frame.coreScale}); opacity:{frame.coreAlpha}"
			></i>

			<div class="jv-boot-word" style="letter-spacing:{frame.letterSpacing}em">
				{#each boot.WORDMARK.split('') as letter, i (i)}
					<span
						style="opacity:{frame.letterAlpha[i]}; filter:blur({frame.letterBlur[i]}px)">{letter}</span
					>
				{/each}
			</div>

			<div class="jv-boot-checks" data-testid="boot-checks">
				{#each frame.checkLines as line, i (i)}
					<span>{line}</span>
				{/each}
			</div>
		</div>
	</div>
{/if}
