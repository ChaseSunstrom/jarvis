<script lang="ts">
	import { page } from '$app/state';

	let { children } = $props();

	const NAV = [
		{ href: '/devices', label: 'DEVICES' },
		{ href: '/areas', label: 'AREAS' },
		{ href: '/automations', label: 'AUTOMATIONS' },
		{ href: '/tools', label: 'TOOLS' },
		{ href: '/settings', label: 'SETTINGS' }
	];

	// The voice HUD owns the whole viewport and paints its own chrome, so the
	// console frame is only drawn for the management routes.
	let isHud = $derived(page.url.pathname === '/');
</script>

{#if isHud}
	{@render children()}
	<a class="hud-console-link" href="/devices" data-testid="console-link">CONSOLE</a>
{:else}
	<div class="console">
		<div class="console-grid" aria-hidden="true"></div>
		<span class="bracket tl" aria-hidden="true"></span>
		<span class="bracket tr" aria-hidden="true"></span>
		<span class="bracket bl" aria-hidden="true"></span>
		<span class="bracket br" aria-hidden="true"></span>

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
						data-testid="nav-{item.label.toLowerCase()}">{item.label}</a
					>
				{/each}
			</nav>
		</header>

		<main class="console-body">
			{@render children()}
		</main>
	</div>
{/if}

<style>
	:global(html, body) {
		margin: 0;
		min-height: 100%;
		background: #04070c;
	}
	:global(*) {
		box-sizing: border-box;
	}

	/* Floating way in to the management UI from the voice HUD. */
	.hud-console-link {
		position: fixed;
		top: 14px;
		left: 50%;
		transform: translateX(-50%);
		z-index: 5;
		padding: 0.3rem 0.9rem;
		border: 1px solid rgba(63, 216, 255, 0.32);
		border-radius: 999px;
		font-family: var(--chrome);
		font-size: 0.6rem;
		letter-spacing: 0.24em;
		color: rgba(63, 216, 255, 0.75);
		text-decoration: none;
		background: rgba(4, 12, 18, 0.6);
		backdrop-filter: blur(2px);
	}
	.hud-console-link:hover {
		color: #eaf7fc;
		border-color: rgba(63, 216, 255, 0.7);
		box-shadow: 0 0 18px rgba(63, 216, 255, 0.25);
	}

	.console {
		--accent: #3fd8ff;
		--chrome: 'SFMono-Regular', ui-monospace, 'Cascadia Code', 'Cascadia Mono', Menlo, Consolas,
			monospace;
		--body: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
		--dim: #8fb3c0;
		--line: rgba(63, 216, 255, 0.32);
		--line-soft: rgba(63, 216, 255, 0.12);
		--panel-bg: rgba(6, 18, 26, 0.72);

		position: relative;
		min-height: 100vh;
		min-height: 100dvh;
		display: grid;
		grid-template-rows: auto 1fr;
		padding: clamp(0.9rem, 2.2vw, 1.8rem);
		gap: clamp(0.8rem, 2vh, 1.4rem);
		color: #d7edf5;
		font-family: var(--body);
		background:
			radial-gradient(ellipse 80% 50% at 50% 0%, rgba(63, 216, 255, 0.1), transparent 70%), #04070c;
	}

	.console-grid {
		position: absolute;
		inset: 0;
		pointer-events: none;
		opacity: 0.45;
		background-image:
			linear-gradient(var(--line-soft) 1px, transparent 1px),
			linear-gradient(90deg, var(--line-soft) 1px, transparent 1px);
		background-size: 46px 46px;
		mask-image: radial-gradient(ellipse 85% 70% at 50% 30%, #000 30%, transparent 90%);
		-webkit-mask-image: radial-gradient(ellipse 85% 70% at 50% 30%, #000 30%, transparent 90%);
	}

	.bracket {
		position: absolute;
		width: clamp(20px, 3vw, 38px);
		height: clamp(20px, 3vw, 38px);
		pointer-events: none;
		border: 2px solid var(--line);
	}
	.bracket.tl {
		top: 10px;
		left: 10px;
		border-right: 0;
		border-bottom: 0;
	}
	.bracket.tr {
		top: 10px;
		right: 10px;
		border-left: 0;
		border-bottom: 0;
	}
	.bracket.bl {
		bottom: 10px;
		left: 10px;
		border-right: 0;
		border-top: 0;
	}
	.bracket.br {
		bottom: 10px;
		right: 10px;
		border-left: 0;
		border-top: 0;
	}

	.console-top {
		position: relative;
		z-index: 1;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0.8rem 1.4rem;
		padding-bottom: 0.7rem;
		border-bottom: 1px solid var(--line-soft);
	}
	.brand {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		text-decoration: none;
	}
	.logo {
		font-family: var(--chrome);
		font-size: clamp(1rem, 2.4vw, 1.35rem);
		font-weight: 600;
		letter-spacing: 0.5em;
		color: var(--accent);
		text-shadow: 0 0 16px rgba(63, 216, 255, 0.45);
	}
	.sub {
		font-family: var(--chrome);
		font-size: 0.55rem;
		letter-spacing: 0.32em;
		color: var(--dim);
		opacity: 0.75;
	}
	nav {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
	}
	nav a {
		font-family: var(--chrome);
		font-size: 0.63rem;
		letter-spacing: 0.2em;
		color: var(--dim);
		text-decoration: none;
		padding: 0.42rem 0.8rem;
		border: 1px solid transparent;
		border-radius: 3px;
		transition:
			color 0.15s,
			border-color 0.15s,
			background 0.15s;
	}
	nav a:hover {
		color: #eaf7fc;
		border-color: var(--line-soft);
	}
	nav a.active {
		color: var(--accent);
		border-color: var(--line);
		background: rgba(63, 216, 255, 0.08);
		box-shadow: 0 0 16px rgba(63, 216, 255, 0.14) inset;
	}

	.console-body {
		position: relative;
		z-index: 1;
		min-width: 0;
		padding-bottom: 2rem;
	}

	/* --- shared console furniture, used by every management page --------- */
	:global(.console h1) {
		font-family: var(--chrome);
		font-size: clamp(0.9rem, 2vw, 1.1rem);
		font-weight: 500;
		letter-spacing: 0.28em;
		color: var(--accent);
		margin: 0 0 0.25rem;
	}
	:global(.console .lede) {
		font-family: var(--chrome);
		font-size: 0.66rem;
		letter-spacing: 0.12em;
		color: var(--dim);
		opacity: 0.8;
		margin: 0 0 1.1rem;
	}
	:global(.console .panel) {
		position: relative;
		border: 1px solid var(--line-soft);
		background: var(--panel-bg);
		border-radius: 4px;
		padding: 0.9rem 1rem;
		margin-bottom: 0.9rem;
	}
	:global(.console .panel-head) {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.8rem;
		font-family: var(--chrome);
		font-size: 0.66rem;
		letter-spacing: 0.24em;
		color: var(--accent);
		text-transform: uppercase;
		margin-bottom: 0.7rem;
		padding-bottom: 0.5rem;
		border-bottom: 1px solid var(--line-soft);
	}
	:global(.console .row) {
		display: flex;
		align-items: center;
		gap: 0.7rem;
		flex-wrap: wrap;
		padding: 0.5rem 0;
		border-bottom: 1px dashed rgba(63, 216, 255, 0.08);
	}
	:global(.console .row:last-child) {
		border-bottom: 0;
	}
	:global(.console .row .name) {
		flex: 1 1 12rem;
		min-width: 0;
	}
	:global(.console .row .name b) {
		font-weight: 500;
		color: #eaf7fc;
	}
	:global(.console .eid) {
		display: block;
		font-family: var(--chrome);
		font-size: 0.58rem;
		line-height: 1.6;
		letter-spacing: 0.08em;
		color: var(--dim);
		opacity: 0.65;
		overflow-wrap: anywhere;
	}
	:global(.console .pill) {
		font-family: var(--chrome);
		font-size: 0.58rem;
		letter-spacing: 0.16em;
		text-transform: uppercase;
		color: var(--dim);
		border: 1px solid var(--line-soft);
		border-radius: 999px;
		padding: 0.15rem 0.55rem;
		white-space: nowrap;
	}
	:global(.console .pill.on) {
		color: var(--accent);
		border-color: var(--line);
		box-shadow: 0 0 12px rgba(63, 216, 255, 0.16);
	}
	:global(.console button.btn),
	:global(.console .btn) {
		font-family: var(--chrome);
		font-size: 0.6rem;
		letter-spacing: 0.16em;
		text-transform: uppercase;
		color: var(--accent);
		background: rgba(63, 216, 255, 0.08);
		border: 1px solid var(--line);
		border-radius: 3px;
		padding: 0.4rem 0.75rem;
		cursor: pointer;
		text-decoration: none;
		transition:
			background 0.15s,
			box-shadow 0.15s,
			color 0.15s;
	}
	:global(.console button.btn:hover),
	:global(.console .btn:hover) {
		background: rgba(63, 216, 255, 0.18);
		box-shadow: 0 0 16px rgba(63, 216, 255, 0.2);
	}
	:global(.console button.btn:disabled) {
		opacity: 0.4;
		cursor: not-allowed;
		box-shadow: none;
	}
	:global(.console button.btn.ghost) {
		color: var(--dim);
		background: transparent;
		border-color: var(--line-soft);
	}
	:global(.console button.btn.danger) {
		color: #ff8f81;
		border-color: rgba(255, 107, 92, 0.4);
		background: rgba(255, 107, 92, 0.08);
	}
	:global(.console button.btn.on) {
		color: #04121a;
		background: var(--accent);
		border-color: var(--accent);
		box-shadow: 0 0 18px rgba(63, 216, 255, 0.4);
	}
	:global(.console input[type='text']),
	:global(.console input[type='number']),
	:global(.console select),
	:global(.console textarea) {
		font-family: var(--chrome);
		font-size: 0.68rem;
		color: #eaf7fc;
		background: rgba(4, 12, 18, 0.85);
		border: 1px solid var(--line-soft);
		border-radius: 3px;
		padding: 0.4rem 0.55rem;
	}
	:global(.console input:focus),
	:global(.console select:focus),
	:global(.console textarea:focus) {
		outline: none;
		border-color: var(--line);
		box-shadow: 0 0 12px rgba(63, 216, 255, 0.18);
	}
	:global(.console input[type='range']) {
		accent-color: var(--accent);
		width: 8rem;
	}
	:global(.console .muted) {
		font-family: var(--chrome);
		font-size: 0.62rem;
		letter-spacing: 0.1em;
		color: var(--dim);
		opacity: 0.75;
	}
	:global(.console .notice) {
		font-family: var(--chrome);
		font-size: 0.63rem;
		letter-spacing: 0.08em;
		color: #ffcf5c;
		border: 1px solid rgba(255, 207, 92, 0.3);
		background: rgba(255, 207, 92, 0.07);
		border-radius: 3px;
		padding: 0.5rem 0.7rem;
		margin-bottom: 0.8rem;
	}
	:global(.console .err) {
		font-family: var(--chrome);
		font-size: 0.63rem;
		letter-spacing: 0.08em;
		color: #ff8f81;
		border: 1px solid rgba(255, 107, 92, 0.32);
		background: rgba(255, 107, 92, 0.07);
		border-radius: 3px;
		padding: 0.5rem 0.7rem;
		margin-bottom: 0.8rem;
	}
	:global(.console pre) {
		font-family: var(--chrome);
		font-size: 0.62rem;
		line-height: 1.5;
		color: #cfe9f3;
		background: rgba(4, 12, 18, 0.85);
		border: 1px solid var(--line-soft);
		border-radius: 3px;
		padding: 0.6rem 0.7rem;
		margin: 0;
		overflow-x: auto;
		max-height: 22rem;
	}
	:global(.console label.check) {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-family: var(--chrome);
		font-size: 0.6rem;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--dim);
		cursor: pointer;
		user-select: none;
	}
	:global(.console label.check input) {
		appearance: none;
		width: 0.85rem;
		height: 0.85rem;
		border: 1px solid var(--line);
		border-radius: 3px;
		background: transparent;
		cursor: pointer;
	}
	:global(.console label.check input:checked) {
		background: var(--accent);
		box-shadow: 0 0 10px rgba(63, 216, 255, 0.55);
	}
</style>
