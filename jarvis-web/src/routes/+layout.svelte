<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';

	import '$lib/styles/tokens.css';
	import '$lib/styles/base.css';
	import '$lib/styles/chrome.css';

	import BootSequence from '$lib/components/BootSequence.svelte';
	import CommandPalette from '$lib/components/CommandPalette.svelte';
	import Toasts from '$lib/components/Toasts.svelte';
	import Approvals from '$lib/components/Approvals.svelte';
	import ToolActivity from '$lib/components/ToolActivity.svelte';
	import TaskDock from '$lib/components/TaskDock.svelte';
	import { ConsoleLink, statusLabel, type LinkSnapshot } from '$lib/consoleLink';
	import { ChordTracker, isBareKey, isPaletteShortcut, isTypingTarget } from '$lib/shortcuts';
	import { applyTextSize, readTextSize } from '$lib/textSize';

	let { children } = $props();

	// The reader's own text size, on every route rather than on the page that
	// sets it: it is one multiplier on the root element (see textSize.ts), and a
	// preference that only applied to the settings page would be a joke.
	onMount(() => applyTextSize(document, readTextSize(localStorage)));

	const NAV = [
		{ href: '/devices', label: 'DEVICES', chord: 'g d' },
		{ href: '/areas', label: 'AREAS', chord: 'g r' },
		{ href: '/automations', label: 'AUTOMATIONS', chord: 'g a' },
		{ href: '/tools', label: 'TOOLS', chord: 'g t' },
		{ href: '/tasks', label: 'TASKS', chord: 'g k' },
		{ href: '/code', label: 'CODE', chord: 'g c' },
		{ href: '/n8n', label: 'N8N', chord: 'g n' },
		{ href: '/settings', label: 'SETTINGS', chord: 'g s' }
	];

	// The voice HUD owns the whole viewport and paints its own chrome, so the
	// console frame is only drawn for the management routes.
	let isHud = $derived(page.url.pathname === '/');

	// --- the console's own link (header indicator + palette index) -----------
	const link = new ConsoleLink();
	let snapshot = $state<LinkSnapshot>(link.current);
	let linkStarted = false;
	const unsubscribe = link.subscribe((s) => (snapshot = s));

	// Derived from the snapshot rather than read once: `link` reconnects on its
	// own, and a surface holding the old Connection would go quietly deaf.
	let approvalConn = $derived(snapshot.status === 'connected' ? link.connection : null);

	/*
	 * The link runs on EVERY route, the HUD included.
	 *
	 * It used to be stopped on `/`, which quietly made the approvals banner a
	 * console-only feature — and approvals are raised by voice turns, which is
	 * to say by the HUD. A tier-3 request that arrives while you are standing in
	 * front of the orb has to be answerable there; expiring unseen because the
	 * only surface that could show it was two navigations away is exactly the
	 * failure the banner exists to prevent.
	 *
	 * What the HUD does not need is the palette index, so it does not load it.
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
			// The HUD has no palette, so it must not eat the key. This used to
			// preventDefault() BEFORE the `isHud` check below, which took
			// Ctrl/Cmd-K away from the browser on `/` and gave nothing back —
			// the palette it toggled is only rendered in the console branch.
			if (isHud) return;
			e.preventDefault();
			paletteOpen = !paletteOpen;
			return;
		}
		// While the palette is up it owns every other key.
		if (paletteOpen || isHud || !isBareKey(e) || e.repeat) return;

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

{#if isHud}
	{@render children()}
	<!--
	  The safety surface, on the bare shell.

	  Docked over the HUD rather than folded into it: the HUD is one object in a
	  dark room and it stays that way, but a held action and a running tool are
	  the two things that must be visible wherever you are standing. Same two
	  components the console renders, same socket, no console chrome around them.
	-->
	<div class="jv-alerts" data-testid="hud-alerts">
		<Approvals conn={approvalConn} />
		<ToolActivity conn={approvalConn} />
		<TaskDock conn={approvalConn} />
	</div>
	<a class="hud-console-link" href="/devices" data-testid="console-link">CONSOLE</a>
{:else}
	<a class="jv-skip" href="#console-main">Skip to content</a>
	<div class="console">
		<div class="jv-grid" aria-hidden="true"></div>
		<span class="jv-bracket tl" aria-hidden="true"></span>
		<span class="jv-bracket tr" aria-hidden="true"></span>
		<span class="jv-bracket bl" aria-hidden="true"></span>
		<span class="jv-bracket br" aria-hidden="true"></span>

		<header class="console-top">
			<a class="brand" href="/" data-testid="hud-link">
				<span class="logo">JARVIS</span>
				<span class="sub">CONSOLE</span>
			</a>

			<nav aria-label="Management sections">
				{#each NAV as item (item.href)}
					<a
						href={item.href}
						class:active={page.url.pathname === item.href}
						aria-current={page.url.pathname === item.href ? 'page' : undefined}
						title="{item.label} ({item.chord})"
						data-testid="nav-{item.label.toLowerCase()}">{item.label}</a
					>
				{/each}
			</nav>

			<div class="console-status">
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
				<span
					class="jv-link"
					data-status={snapshot.status}
					data-testid="link-status"
					role="status"
					aria-live="polite"
					aria-label="Backend link {statusLabel(snapshot.status)}"
				>
					<span class="jv-link-dot" aria-hidden="true"></span>
					<span class="jv-link-text">{statusLabel(snapshot.status)}</span>
				</span>
			</div>
		</header>

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
			     HUD is still going three navigations later, and this is the only
			     thing on any page that says so. -->
			<TaskDock conn={approvalConn} />
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
	/* Floating way in to the management UI from the voice HUD. */
	.hud-console-link {
		position: fixed;
		top: 14px;
		left: 50%;
		transform: translateX(-50%);
		z-index: 5;
		padding: 0.3rem 0.9rem;
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-pill);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-accent);
		text-decoration: none;
		background: rgba(4, 12, 18, 0.6);
		backdrop-filter: blur(2px);
		transition:
			color var(--jv-dur-fast) var(--jv-ease-out),
			border-color var(--jv-dur-fast) var(--jv-ease-out),
			box-shadow var(--jv-dur-fast) var(--jv-ease-out);
	}
	.hud-console-link:hover {
		color: var(--jv-text-bright);
		border-color: var(--jv-accent);
		box-shadow: var(--jv-glow-md);
	}

	.brand {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		text-decoration: none;
	}
	.logo {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xl);
		font-weight: 600;
		letter-spacing: var(--jv-track-logo);
		color: var(--jv-accent);
		text-shadow: var(--jv-glow-md);
	}
	.sub {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-dim);
	}

	nav {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem;
		min-width: 0;
	}

	/*
	 * Inside the Android app's console frame, this page is not the chrome.
	 *
	 * ManagementActivity already draws the origin, a RELOAD and the console's
	 * own sections as a native tab strip — it has to, because a link tapped in
	 * a WebView is a page-initiated navigation and does not carry the bearer
	 * header. So the page's copy of that nav is a second row of tabs that
	 * cannot work, and the JARVIS/CONSOLE wordmark repeats a title bar an inch
	 * above it.
	 *
	 * The status readout and the palette button stay: neither is duplicated by
	 * the native frame, and the link indicator is the one thing on this header
	 * that says whether the console is talking to anything.
	 *
	 * See src/app.html for where the marker comes from.
	 */
	:global(html[data-embed='android']) nav[aria-label='Management sections'],
	:global(html[data-embed='android']) .brand {
		display: none;
	}
	:global(html[data-embed='android']) .console-top {
		/* Without a brand to sit beside, the status must not float mid-header. */
		justify-content: flex-end;
	}
	nav a {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		letter-spacing: 0.2em;
		color: var(--jv-text-dim);
		text-decoration: none;
		padding: 0.42rem 0.7rem;
		border: 1px solid transparent;
		border-radius: var(--jv-radius-sm);
		white-space: nowrap;
		position: relative;
		transition:
			color var(--jv-dur-fast) var(--jv-ease-out),
			border-color var(--jv-dur-fast) var(--jv-ease-out),
			background var(--jv-dur-fast) var(--jv-ease-out);
	}
	nav a:hover {
		color: var(--jv-text-bright);
		border-color: var(--jv-line-soft);
	}
	nav a.active {
		color: var(--jv-accent);
		border-color: var(--jv-line);
		background: var(--jv-wash);
		box-shadow: var(--jv-glow-sm) inset;
	}
	/* The current route also gets a lit underline, so it does not rely on colour alone. */
	nav a.active::after {
		content: '';
		position: absolute;
		left: 0.6rem;
		right: 0.6rem;
		bottom: -0.42rem;
		height: 2px;
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-md);
	}

	.console-status {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
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
		gap: 0.4rem;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		color: var(--jv-text-dim);
		background: transparent;
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-pill);
		padding: 0.24rem 0.7rem;
		cursor: pointer;
		transition:
			color var(--jv-dur-fast) var(--jv-ease-out),
			border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.palette-open:hover {
		color: var(--jv-accent);
		border-color: var(--jv-line);
	}

	.console-body:focus {
		outline: none;
	}

	@media (max-width: 720px) {
		.brand .sub {
			display: none;
		}
		nav {
			order: 3;
			flex: 1 1 100%;
			/* min-width:0 is what actually lets it scroll instead of stretching
			   the header (and the page) to its content width. */
			min-width: 0;
			flex-wrap: nowrap;
			overflow-x: auto;
			padding-bottom: 0.2rem;
			scrollbar-width: none;
		}
		nav::-webkit-scrollbar {
			display: none;
		}
		nav a.active::after {
			bottom: 0;
		}
		.palette-open span[aria-hidden='true'] {
			display: none;
		}
	}
</style>
