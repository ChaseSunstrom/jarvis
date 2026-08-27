<script lang="ts">
	import { setContext } from 'svelte';
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { NAV_SCREENS } from '$lib/screens';
	import { hudStatus } from '$lib/hudStatus.svelte';

	import '$lib/styles/fonts.css';
	import '$lib/styles/tokens.css';
	import '$lib/styles/base.css';
	import '$lib/styles/chrome.css';

	import { StatusReadout, TopBar } from '$lib/ui';
	import BootSequence from '$lib/components/BootSequence.svelte';
	import CommandPalette from '$lib/components/CommandPalette.svelte';
	import Toasts from '$lib/components/Toasts.svelte';
	import Approvals from '$lib/components/Approvals.svelte';
	import ToolActivity from '$lib/components/ToolActivity.svelte';
	import TaskDock from '$lib/components/TaskDock.svelte';
	import Notifications from '$lib/components/Notifications.svelte';
	import { ConsoleLink, statusLabel, type LinkSnapshot } from '$lib/consoleLink';
	import { ChordTracker, isBareKey, isPaletteShortcut, isTypingTarget } from '$lib/shortcuts';
	import { applyTextSize, readTextSize } from '$lib/textSize';

	let { children } = $props();

	// The reader's own text size, on every route rather than on the page that
	// sets it: it is one multiplier on the root element (see textSize.ts), and a
	// preference that only applied to the settings page would be a joke.
	onMount(() => applyTextSize(document, readTextSize(localStorage)));

	/*
	 * The one bar, from the one place a route is declared.
	 *
	 * Five tabs: the voice screen and the four console destinations, as
	 * Reactor II draws them (`docs/design/c2-reactor.html`). The voice screen
	 * used to paint its own chrome and reach the console through a floating
	 * CONSOLE pill, and the two did not look like one product (M49).
	 */
	const NAV = NAV_SCREENS.map((screen) => ({
		href: screen.path,
		label: screen.name.toUpperCase(),
		chord: screen.chord ?? '',
		testid: `nav-${screen.name.toLowerCase()}`
	}));

	/**
	 * Is the current URL inside this destination?
	 *
	 * Prefix, not equality. A destination's own path redirects to its first
	 * section, so the user is never AT `/house` — they are at `/house/devices`,
	 * and an exact match left every tab unlit the moment the consolidation
	 * landed (M48). `/` is the exception: everything starts with it.
	 */
	function inDestination(href: string): boolean {
		const here = page.url.pathname;
		if (href === '/') return here === '/';
		return here === href || here.startsWith(href + '/');
	}

	// The voice screen owns the viewport under the bar; the console frame is
	// only drawn for the management routes.
	let isHud = $derived(page.url.pathname === '/');

	// --- the console's own link (header indicator + palette index) -----------
	const link = new ConsoleLink();
	let snapshot = $state<LinkSnapshot>(link.current);
	let linkStarted = false;
	const unsubscribe = link.subscribe((s) => (snapshot = s));

	// Derived from the snapshot rather than read once: `link` reconnects on its
	// own, and a surface holding the old Connection would go quietly deaf.
	let approvalConn = $derived(snapshot.status === 'connected' ? link.connection : null);
	// The voice screen draws the graph and the activity strip off the same
	// link (it has no link of its own — its socket is the pipeline's). A getter,
	// not the value: context is set once, the connection comes and goes.
	setContext('console-connection', () => approvalConn);

	/*
	 * The link runs on EVERY route, the voice screen included.
	 *
	 * It used to be stopped on `/`, which quietly made the approvals banner a
	 * console-only feature — and approvals are raised by voice turns, which is
	 * to say by the voice screen. A tier-3 request that arrives while you are
	 * standing in front of the reactor has to be answerable there; expiring
	 * unseen because the only surface that could show it was two navigations
	 * away is exactly the failure the banner exists to prevent.
	 *
	 * What the voice screen does not need is the palette index, so it does not
	 * load it.
	 */
	$effect(() => {
		link.setIndexed(!isHud);
		if (!linkStarted) {
			link.start();
			linkStarted = true;
		}
	});

	onDestroy(() => {
		unsubscribe();
		link.stop();
	});

	/** The link's tone for the readout: lit, amber while it dials, off when it is down. */
	const linkTone = $derived(
		snapshot.status === 'connected'
			? ('live' as const)
			: snapshot.status === 'connecting' || snapshot.status === 'reconnecting'
				? ('warn' as const)
				: ('off' as const)
	);

	// --- keyboard ------------------------------------------------------------
	let paletteOpen = $state(false);
	let chordPrefix = $state('');
	const chords = new ChordTracker();

	function focusPrimaryFilter(): boolean {
		if (typeof document === 'undefined') return false;
		const el =
			document.querySelector<HTMLInputElement>('[data-jv-filter]') ??
			document.querySelector<HTMLInputElement>('.console-body input[type="text"]');
		if (!el) return false;
		el.focus();
		el.select?.();
		return true;
	}

	function onWindowKeyDown(e: KeyboardEvent): void {
		if (isPaletteShortcut(e)) {
			// The voice screen has no palette, so it must not eat the key. This
			// used to preventDefault() BEFORE the `isHud` check below, which took
			// Ctrl/Cmd-K away from the browser on `/` and gave nothing back —
			// the palette it toggled is only rendered in the console branch.
			if (isHud) return;
			e.preventDefault();
			paletteOpen = !paletteOpen;
			return;
		}
		// While the palette is up it owns every other key.
		if (paletteOpen || !isBareKey(e) || e.repeat) return;

		if (e.key === 'Escape') {
			chords.reset();
			chordPrefix = '';
			(document.activeElement as HTMLElement | null)?.blur?.();
			return;
		}
		if (isTypingTarget(e.target as any)) return;

		if (e.key === '/') {
			if (focusPrimaryFilter()) e.preventDefault();
			return;
		}

		// The chords work on the voice screen too now that it wears the same
		// bar: `g d` from the reactor reaches devices, `g h` from anywhere comes
		// back. A typing target is still respected — see above.
		const result = chords.press(e.key);
		chordPrefix = result.prefix;
		if (result.href) {
			e.preventDefault();
			void goto(result.href);
		}
	}
</script>

<svelte:window onkeydown={onWindowKeyDown} />

<BootSequence />
<Toasts />

{#if !isHud}
	<!-- First tab stop on every console page, ahead of the bar. -->
	<a class="jv-skip" href="#console-main">Skip to content</a>
{/if}

<TopBar tabs={NAV} isCurrent={inDestination}>
	{#snippet status()}
		{#if isHud}
			<!-- The pipeline's state, written by the page into one shared cell. -->
			<StatusReadout
				items={[
					{
						label: hudStatus.label,
						tone: hudStatus.tone,
						busy: hudStatus.busy,
						testid: 'status',
						role: 'status'
					}
				]}
			/>
		{:else}
			{#if chordPrefix}
				<span class="chord" data-testid="chord-hint" aria-hidden="true">{chordPrefix} …</span>
			{/if}
			<button
				type="button"
				class="palette-open"
				data-testid="palette-open"
				aria-label="Open the command palette"
				aria-keyshortcuts="Control+K Meta+K"
				onclick={() => (paletteOpen = true)}
			>
				<span aria-hidden="true">⌘K</span> SEARCH
			</button>
			<StatusReadout
				items={[
					{
						label: statusLabel(snapshot.status),
						tone: linkTone,
						testid: 'link-status',
						status: snapshot.status,
						role: 'status',
						title: `Backend link ${statusLabel(snapshot.status)}`
					}
				]}
			/>
		{/if}
	{/snippet}
</TopBar>

{#if isHud}
	{@render children()}
	<!--
	  The safety surface, over the voice screen.

	  Docked under the bar rather than folded into the page: a held action and
	  a running tool are the two things that must be visible wherever you are
	  standing. Same two components the console renders, same socket.
	-->
	<div class="jv-alerts" data-testid="hud-alerts">
		<Approvals conn={approvalConn} />
		<ToolActivity conn={approvalConn} />
		<!-- The task dock is the voice page's own here (M76): under the
		     instrument, where the operator asked for it, not over it. A held
		     action and the tool strip stay up here, where an approval must be. -->
	</div>
{:else}
	<div class="console">
		<main class="console-body" id="console-main" tabindex="-1">
			<!-- Above the routed page and outside the {#key} block: an approval
			     must survive navigation, because the action is still waiting
			     whatever page you wandered to. -->
			<Approvals conn={approvalConn} />
			<!-- Same reasoning as the approvals banner: a turn keeps running
			     while you navigate, so what it is doing has to be visible
			     wherever you are. -->
			<ToolActivity conn={approvalConn} />
			<!-- And for the same reason again: a research run started from the
			     voice screen is still going three navigations later, and this
			     is the only thing on any page that says so. -->
			<TaskDock conn={approvalConn} />
			<!-- And what Jarvis said while nobody was looking. Collapsed by
			     default with an unread count: these arrive when you are not at
			     the screen, which is exactly why a toast is the wrong shape for
			     them. -->
			<Notifications conn={approvalConn} />
			{#key page.url.pathname}
				<div class="jv-route" data-testid="route" data-route={page.url.pathname}>
					{@render children()}
				</div>
			{/key}
		</main>
	</div>

	<CommandPalette
		open={paletteOpen}
		source={snapshot}
		call={(domain, service, data) => link.callService(domain, service, data)}
		onClose={() => (paletteOpen = false)}
	/>
{/if}

<style>
	/*
	 * Inside the Android app's console frame, this page is not the chrome.
	 *
	 * ManagementActivity already draws the origin, a RELOAD and the console's
	 * own front doors as a native tab strip — it has to, because a link tapped
	 * in a WebView is a page-initiated navigation and does not carry the
	 * bearer header. So the bar's tabs are a second row that cannot work, and
	 * the wordmark repeats a title bar an inch above it.
	 *
	 * The readout and the palette button stay: neither is duplicated by the
	 * native frame, and the link indicator is the one thing on this bar that
	 * says whether the console is talking to anything.
	 *
	 * See src/app.html for where the marker comes from.
	 */
	:global(html[data-embed='android'] nav[aria-label='Management sections']),
	:global(html[data-embed='android'] .brand) {
		display: none;
	}

	.chord {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-gold);
	}
	.palette-open {
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-2);
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		background: transparent;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-1) var(--jv-space-3);
		cursor: pointer;
		white-space: nowrap;
		transition: color var(--jv-dur-fast) var(--jv-ease-out),
			border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.palette-open span[aria-hidden='true'] {
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		color: var(--jv-text-faint);
	}
	.palette-open:hover {
		color: var(--jv-text-bright);
		border-color: var(--jv-line);
	}

	/*
	 * The alert dock, on the voice screen.
	 *
	 * Fixed under the bar, as wide as it needs to be and no wider, and gone
	 * entirely when neither component has anything to say (both render nothing
	 * when idle, so the dock collapses to zero height). `pointer-events` is off
	 * on the dock and back on for its contents, or an empty dock would sit
	 * over the reactor swallowing clicks.
	 */
	.jv-alerts {
		position: fixed;
		top: calc(var(--jv-space-7) + var(--jv-space-4));
		left: 50%;
		transform: translateX(-50%);
		z-index: 50;
		width: min(calc(var(--jv-space-7) * 15.3333), calc(100vw - var(--jv-space-5)));
		pointer-events: none;
	}
	.jv-alerts > :global(*) {
		pointer-events: auto;
	}

	.console-body:focus {
		outline: none;
	}

	@media (max-width: 720px) {
		.palette-open span[aria-hidden='true'] {
			display: none;
		}
	}
</style>
