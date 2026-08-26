<!--
@component
Reactor II's top bar, the same on every screen: the brand at the left, the
tabs centred with one accent underline that slides to the current one, and a
readout at the right. Drawn by the root layout on the voice screen and the
console alike — the voice screen used to paint its own chrome and reach the
console through a floating pill, and the two did not look like one product.

`tabs` is the console's `NAV_SCREENS`; `isCurrent` decides which is lit (a
prefix match, because a destination redirects to its first section). The
`status` snippet is whatever the screen has to say about itself.

```svelte
<TopBar tabs={NAV} isCurrent={(href) => here.startsWith(href)}>
	{#snippet status()}<StatusReadout items={…} />{/snippet}
</TopBar>
```

Inside the Android app's console frame the brand and the tabs are hidden by
the root layout (the native strip already draws them); the readout stays.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import { onMount, tick } from 'svelte';

	export interface Tab {
		href: string;
		label: string;
		/** The keyboard chord, for the tooltip. */
		chord?: string;
		testid: string;
		/** A count beside the label. */
		count?: number;
		/** Something in there is happening now. */
		live?: boolean;
	}
	interface Props {
		tabs: Tab[];
		isCurrent: (href: string) => boolean;
		status?: Snippet;
		testid?: string;
	}
	let { tabs, isCurrent, status, testid = 'top-bar' }: Props = $props();

	let nav = $state<HTMLElement | null>(null);
	let indicator = $state({ left: 0, width: 0, ready: false });
	/**
	 * Which edges of the strip hide more tabs, on a phone.
	 *
	 * Six words do not fit 360px, so the strip scrolls — and a scroll with
	 * no scrollbar and nothing cut mid-word is invisible: with five tabs the
	 * fifth was clipped and read as absent, with six (M62) SETTINGS was off
	 * the edge entirely. The overflowing edge fades, and the current tab is
	 * scrolled into view, so what is there can be seen to be there.
	 */
	let fade = $state({ left: false, right: false });

	/**
	 * Put the underline under the current tab.
	 *
	 * Measured rather than drawn per tab so there is ONE underline that moves,
	 * which is what makes a tab change read as "the same thing, elsewhere"
	 * instead of one light going off and another coming on.
	 */
	async function place(): Promise<void> {
		await tick();
		const host = nav;
		if (!host) return;
		const current = host.querySelector<HTMLElement>('a[aria-current="page"]');
		if (!current) {
			indicator = { left: 0, width: 0, ready: true };
			return;
		}
		indicator = { left: current.offsetLeft, width: current.offsetWidth, ready: true };
		if (host.scrollWidth > host.clientWidth + 1) {
			current.scrollIntoView({ block: 'nearest', inline: 'nearest' });
		}
		edges();
	}

	function edges(): void {
		const host = nav;
		if (!host) return;
		const more = host.scrollWidth > host.clientWidth + 1;
		fade = {
			left: more && host.scrollLeft > 1,
			right: more && host.scrollLeft + host.clientWidth < host.scrollWidth - 1
		};
	}

	const currentHref = $derived(tabs.find((t) => isCurrent(t.href))?.href ?? '');
	$effect(() => {
		// Tracked: the current tab, AND the nav element, which is bound after
		// the first run of this effect on hydration — without it the underline
		// was placed against nothing and never placed again.
		void currentHref;
		void nav;
		void place();
	});

	onMount(() => {
		// The fonts can land after first paint and change every tab's width.
		const refit = () => void place();
		refit();
		window.addEventListener('resize', refit);
		document.fonts?.ready.then(refit);
		// And anything else that moves a tab — a late font swap that
		// `fonts.ready` resolved ahead of, a readout that grew, the strip
		// scrolling on a phone. Measured from the tabs themselves: on a slow
		// runner the underline sat 11px left of VOICE, placed against a
		// fallback face the swap then replaced (home.spec, CI, ca6c57c).
		const observer =
			typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(refit);
		if (observer && nav) {
			observer.observe(nav);
			for (const tab of nav.querySelectorAll('a')) observer.observe(tab);
		}
		return () => {
			window.removeEventListener('resize', refit);
			observer?.disconnect();
		};
	});
</script>

<header class="top" data-testid={testid}>
	<a class="brand" href="/" data-testid="hud-link" aria-label="Jarvis — the voice screen">
		<svg viewBox="0 0 18 18" aria-hidden="true" class="mark">
			<circle cx="9" cy="9" r="8" fill="none" stroke="currentColor" stroke-width="1" stroke-dasharray="6 3" />
			<circle cx="9" cy="9" r="3.5" fill="none" stroke="currentColor" stroke-width="1" />
			<circle cx="9" cy="9" r="1.2" fill="currentColor" />
		</svg>
		<span class="word">JARVIS</span>
		<small>local</small>
	</a>

	<nav
		class="tabs"
		class:fade-l={fade.left}
		class:fade-r={fade.right}
		aria-label="Management sections"
		bind:this={nav}
		onscroll={edges}
	>
		{#each tabs as tab (tab.href)}
			<a
				href={tab.href}
				class:on={isCurrent(tab.href)}
				aria-current={isCurrent(tab.href) ? 'page' : undefined}
				title={tab.chord ? `${tab.label} (${tab.chord})` : tab.label}
				data-testid={tab.testid}
			>
				{tab.label}
				{#if tab.count !== undefined}<b>{tab.count}</b>{/if}
				{#if tab.live}<i class="live" aria-hidden="true"></i>{/if}
			</a>
		{/each}
		<span
			class="ind"
			class:ready={indicator.ready}
			style:left="{indicator.left}px"
			style:width="{indicator.width}px"
			aria-hidden="true"
			data-testid="nav-underline"
		></span>
	</nav>

	<div class="status">
		{#if status}{@render status()}{/if}
	</div>
</header>

<style>
	.top {
		position: sticky;
		top: 0;
		z-index: 20;
		display: grid;
		/* The tabs take what the brand and the status leave, and scroll inside
		   it: with `1fr auto 1fr` the side columns collapsed to nothing when six
		   tabs did not fit a tablet, and VOICE was drawn over the brand. */
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: stretch;
		gap: var(--jv-space-4);
		min-height: calc(var(--jv-space-7) + var(--jv-space-2));
		padding: 0 var(--jv-space-6);
		border-bottom: 1px solid var(--jv-line-hair);
		background: linear-gradient(var(--jv-bg), color-mix(in srgb, var(--jv-bg) 60%, transparent));
		backdrop-filter: blur(8px);
		white-space: nowrap;
	}
	.brand {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		min-width: 0;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-sm);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-bright);
		text-decoration: none;
	}
	.brand .mark {
		width: var(--jv-space-5);
		height: var(--jv-space-5);
		color: var(--jv-accent);
		flex: none;
	}
	.brand small {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
	}
	.tabs {
		position: relative;
		display: flex;
		align-items: stretch;
		gap: var(--jv-space-5);
		min-width: 0;
		justify-self: stretch;
		/* `safe`: a centred row that overflows must still scroll to its start. */
		justify-content: safe center;
		overflow-x: auto;
		scrollbar-width: none;
		scroll-snap-type: x proximity;
	}
	.tabs::-webkit-scrollbar {
		display: none;
	}
	/* An edge that hides more tabs fades, so what is there can be seen to be
	   there; a scroll with no scrollbar and nothing cut mid-word is invisible. */
	.tabs.fade-r {
		mask-image: linear-gradient(
			to right,
			var(--jv-bg) calc(100% - var(--jv-space-7)),
			transparent
		);
	}
	.tabs.fade-l {
		mask-image: linear-gradient(to right, transparent, var(--jv-bg) var(--jv-space-7));
	}
	.tabs.fade-l.fade-r {
		mask-image: linear-gradient(
			to right,
			transparent,
			var(--jv-bg) var(--jv-space-7),
			var(--jv-bg) calc(100% - var(--jv-space-7)),
			transparent
		);
	}
	.tabs a {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		padding: 0 var(--jv-space-1);
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-chrome);
		scroll-snap-align: start;
		text-transform: uppercase;
		color: var(--jv-text-dim);
		text-decoration: none;
		transition: color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.tabs a:hover {
		color: var(--jv-text);
	}
	.tabs a.on {
		color: var(--jv-text-bright);
	}
	.tabs a b {
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		padding: 0 var(--jv-space-1);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
	}
	.tabs i.live {
		width: var(--jv-space-1);
		height: var(--jv-space-1);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-accent);
		box-shadow: 0 0 var(--jv-radius-md) var(--jv-glow);
		animation: blink var(--jv-dur-blink) var(--jv-ease-in-out) infinite;
	}
	/* The one underline. */
	.ind {
		position: absolute;
		bottom: 0;
		height: var(--jv-rule-live);
		background: var(--jv-accent);
		opacity: 0;
	}
	.ind.ready {
		opacity: 1;
		transition: left var(--jv-dur-base) var(--jv-ease-out), width var(--jv-dur-base) var(--jv-ease-out);
	}
	.status {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--jv-space-3);
		min-width: 0;
	}
	@keyframes blink {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0.35;
		}
	}
	/* Up to a tablet the tabs take a row of their own under the brand and the
	   status: six words fit 768px across, but not beside two other things. */
	@media (max-width: 1023px) {
		.top {
			grid-template-columns: auto 1fr;
			grid-template-rows: auto auto;
			gap: 0 var(--jv-space-3);
			padding: var(--jv-space-2) var(--jv-space-3) 0;
		}
		.brand {
			grid-row: 1;
		}
		.status {
			grid-row: 1;
			grid-column: 2;
		}
		.tabs {
			grid-row: 2;
			grid-column: 1 / -1;
			padding: var(--jv-space-2) 0 var(--jv-space-2);
		}
	}
	@media (max-width: 720px) {
		.brand small {
			display: none;
		}
		.tabs {
			gap: var(--jv-space-4);
		}
		.tabs a {
			padding: var(--jv-space-1) var(--jv-space-1);
			/* Six words across a phone: the chrome tracking gives way, and a
			   tab snaps whole into view rather than stopping mid-word. */
			font-size: var(--jv-fs-2xs);
			letter-spacing: var(--jv-track-tight);
		}
	}
</style>
