<script lang="ts">
	/**
	 * Put a phone on the house without typing a token into it.
	 *
	 * Forty characters of base64 typed on a phone keyboard is the worst moment
	 * of setting Jarvis up, and the obvious shortcut — put the token in the QR —
	 * is worse than typing it: a QR on a screen can be photographed from across
	 * the room, ends up in whatever screenshot captured it, and stays valid as
	 * long as the token does.
	 *
	 * So the QR carries a short-lived, single-use CODE, and the app exchanges it
	 * for a token over HTTP. See `jarvis-core/jarvis/api/pairing.py`.
	 *
	 * The address in the QR defaults to the origin this page is being served on,
	 * because that is demonstrably an address that reaches Jarvis from a browser
	 * — and it is the one the app is told to use. It is editable, because the
	 * console may be open on `localhost` while the phone needs the LAN address,
	 * and only a person can know which.
	 */
	import { onDestroy } from 'svelte';
	import { qrSvg } from '$lib/qr';
	import { describeError } from '$lib/connection';

	let code = $state('');
	let expiresAt = $state(0);
	let url = $state('');
	let err = $state('');
	let busy = $state(false);
	let now = $state(Date.now());

	/** Seconds a code has left, or 0. Drives both the readout and the expiry. */
	const secondsLeft = $derived(expiresAt ? Math.max(0, Math.round(expiresAt - now / 1000)) : 0);
	const live = $derived(Boolean(code) && secondsLeft > 0);

	/**
	 * `jarvis://pair?v=1&u=<url>&c=<code>` — what `PairingPayload.kt` parses.
	 *
	 * Both values are percent-encoded. The URL contains `:` and `/`, and a code
	 * is base64url which is already safe, but encoding both means the format is
	 * one rule rather than two and a future code alphabet cannot break it.
	 */
	const payload = $derived(
		live ? `jarvis://pair?v=1&u=${encodeURIComponent(url)}&c=${encodeURIComponent(code)}` : ''
	);

	const svg = $derived(payload ? qrSvg(payload, { title: 'Jarvis pairing code' }) : '');

	async function issue(): Promise<void> {
		busy = true;
		err = '';
		try {
			const res = await fetch('/api/pair', { method: 'POST' });
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.message ?? `pairing failed (${res.status})`);
			}
			const body = await res.json();
			code = body.code;
			expiresAt = body.expires_at ?? Date.now() / 1000 + (body.ttl ?? 300);
		} catch (e) {
			err = describeError(e);
			code = '';
		} finally {
			busy = false;
		}
	}

	function forget(): void {
		code = '';
		expiresAt = 0;
	}

	$effect(() => {
		if (!url && typeof location !== 'undefined') url = location.origin;
	});

	const ticker = setInterval(() => {
		now = Date.now();
		// Stop drawing a code the server will no longer honour. A QR that looks
		// live and is not sends somebody hunting a camera problem.
		if (code && secondsLeft <= 0) forget();
	}, 1000);
	onDestroy(() => clearInterval(ticker));
</script>

<section class="panel" data-testid="pairing">
	<div class="panel-head">
		<span>Pair a phone</span>
		{#if live}
			<span class="muted" data-testid="pair-expiry">{secondsLeft}s left</span>
		{/if}
	</div>

	<p class="muted">
		Scan this in the Jarvis app — SETTINGS → SCAN QR. The code is single-use and lasts five
		minutes; it is not a token, so a photograph of this screen is worthless once it expires.
	</p>

	<div class="row">
		<span class="name"><b>Address</b><span class="eid">what the phone will connect to</span></span>
		<input
			type="text"
			aria-label="Server address for the phone"
			data-testid="pair-url"
			bind:value={url}
			placeholder="http://jarvis.local:8199"
		/>
	</div>

	{#if err}<p class="err" data-testid="pair-error" role="alert">{err}</p>{/if}

	{#if live}
		<div class="qr" data-testid="pair-qr">
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- qrSvg emits its
			     own markup from numbers; nothing user-supplied reaches it unescaped. -->
			{@html svg}
		</div>
		<p class="muted small" data-testid="pair-payload">{payload}</p>
	{/if}

	<div class="row">
		<button type="button" class="btn" data-testid="pair-new" disabled={busy} onclick={issue}>
			{live ? 'NEW CODE' : 'SHOW CODE'}
		</button>
		{#if live}
			<button type="button" class="btn ghost" data-testid="pair-hide" onclick={forget}>
				HIDE
			</button>
		{/if}
	</div>
</section>

<style>
	.qr {
		display: flex;
		justify-content: center;
		padding: var(--jv-space-3) 0;
	}
	.qr :global(svg) {
		width: min(260px, 60vw);
		height: auto;
		/* The quiet zone is drawn by qrSvg; this only keeps the light modules
		   readable when the console is in its dark theme. */
		background: #fff;
		border-radius: var(--jv-radius-sm);
	}
	.small {
		font-size: var(--jv-fs-xs);
		word-break: break-all;
	}
</style>
