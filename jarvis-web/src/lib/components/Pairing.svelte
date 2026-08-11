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
	import { onDestroy, onMount } from 'svelte';
	import { qrSvg } from '$lib/qr';
	import { describeError, openConnection, type Connection } from '$lib/connection';
	import { toasts } from '$lib/toast';
	import type { AccessToken } from '$lib/jarvisClient';

	/**
	 * The operator's pairing secret. Typed here, forwarded, never stored.
	 *
	 * Not an inconvenience — the whole reason minting needs it is that this
	 * console's relay hands the admin token to anything that connects, so
	 * possession of the API token deliberately is not enough to mint a
	 * credential. See `jarvis-core/jarvis/api/pairing.py`.
	 */
	let secret = $state('');
	let code = $state('');
	/**
	 * The name of the credential that claimed the last code, once one has.
	 *
	 * Single-use is enforced on the server — `PairingCodes.claim` deletes the
	 * entry it matched — but until this existed the console never SAID so. A QR
	 * that stays on screen after it has been spent looks exactly like a QR that
	 * can be scanned again, which is the same screen the operator is looking at
	 * while wondering why the second phone did not pair.
	 */
	let claimedBy = $state('');
	/** Consecutive codes that expired without anybody scanning them. */
	let unclaimed = $state(0);
	let expiresAt = $state(0);
	let url = $state('');
	let err = $state('');
	let busy = $state(false);
	let now = $state(Date.now());

	/**
	 * What may talk to this house, so a phone can be un-paired.
	 *
	 * Pairing without un-pairing is a one-way door: a phone that is lost, sold
	 * or simply no longer trusted has to be removable, and until this existed
	 * the only way was to edit the token store by hand on the server.
	 */
	let tokens = $state<AccessToken[]>([]);
	let tokensSupported = $state(true);
	let conn = $state<Connection | null>(null);
	let revoking = $state('');

	/** Credentials that existed when the current code was minted. */
	let known = new Set<string>();

	/** Where the operator's secret lives for the rest of this tab's life. */
	const SECRET_KEY = 'jarvis.pairing.secret';

	/**
	 * How many codes may expire unscanned before the console stops replacing
	 * them by itself. A tab left open on this page overnight should not mint a
	 * code every five minutes forever; three is enough to cover somebody
	 * walking to fetch the phone.
	 */
	const MAX_AUTO_REISSUE = 3;

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

	/**
	 * Mint one.
	 *
	 * Kept to a single click, which is the whole point of remembering the
	 * secret: the operator types it once per browser session and every code
	 * after that is one press. The secret still never leaves this tab except on
	 * the way to `/api/pair`, and never reaches jarvis-web's own environment —
	 * it is the second factor precisely because reaching this console is enough
	 * to be an authenticated API client, so storing it on the server would
	 * quietly delete the property it exists for.
	 */
	async function issue(): Promise<void> {
		if (!secret.trim() || busy) return;
		busy = true;
		err = '';
		claimedBy = '';
		try {
			const res = await fetch('/api/pair', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ secret })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.message ?? `pairing failed (${res.status})`);
			}
			const body = await res.json();
			code = body.code;
			expiresAt = body.expires_at ?? Date.now() / 1000 + (body.ttl ?? 300);
			// Whatever is on the house right now is the baseline; anything that
			// appears after this is what scanned the code. Re-read rather than
			// trusted from before, so a device paired from another console does
			// not read as this code being claimed.
			if (conn) await loadTokens(conn);
			known = new Set(tokens.map((t) => t.id));
			try {
				sessionStorage.setItem(SECRET_KEY, secret);
			} catch {
				// A browser with storage disabled still works; it just asks for
				// the secret again next reload.
			}
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
		unclaimed = 0;
	}

	async function loadTokens(connection: Connection): Promise<void> {
		try {
			tokens = (await connection.client.listTokens()) ?? [];
			// A credential that was not here when the code was minted is the
			// phone that just scanned it. The code is already gone server-side;
			// this is only how the screen finds out.
			if (code) {
				const fresh = tokens.find((t) => !known.has(t.id));
				if (fresh) {
					claimedBy = fresh.name;
					unclaimed = 0;
					code = '';
					expiresAt = 0;
					toasts.success(`Paired ${fresh.name}`, 'That code is spent — generate another for the next device.');
				}
			}
		} catch {
			// An older jarvis-core has no such command. The pairing half above
			// still works, so this hides rather than shouting.
			tokens = [];
			tokensSupported = false;
		}
	}

	async function revoke(row: AccessToken): Promise<void> {
		const connection = conn;
		if (!connection) return;
		revoking = row.id;
		try {
			const result = await connection.client.revokeToken(row.id);
			toasts.success(
				`Revoked ${row.name}`,
				result?.sockets_closed
					? 'Its open connection was closed as well.'
					: 'It was not connected.'
			);
			await loadTokens(connection);
		} catch (e) {
			toasts.error(`Could not revoke ${row.name}`, describeError(e));
		} finally {
			revoking = '';
		}
	}

	onMount(() => {
		let disposed = false;
		(async () => {
			try {
				const connection = await openConnection({});
				if (disposed) {
					connection.close();
					return;
				}
				conn = connection;
				await loadTokens(connection);
				// Typed once per tab, then every code after it is one press.
				// sessionStorage rather than localStorage: it dies with the tab,
				// so a shared machine does not keep the operator's second factor
				// on disk.
				try {
					secret = sessionStorage.getItem(SECRET_KEY) ?? '';
				} catch {
					secret = '';
				}
				if (secret) await issue();
			} catch {
				tokensSupported = false;
			}
		})();
		return () => {
			disposed = true;
			conn?.close();
			conn = null;
		};
	});

	$effect(() => {
		if (!url && typeof location !== 'undefined') url = location.origin;
	});

	const ticker = setInterval(() => {
		now = Date.now();
		// Stop drawing a code the server will no longer honour. A QR that looks
		// live and is not sends somebody hunting a camera problem.
		if (code && secondsLeft <= 0) {
			code = '';
			expiresAt = 0;
			// Replace it, so the panel is never sitting there with a dead code
			// and a button the operator has to notice. Bounded, so a tab left
			// open overnight stops after MAX_AUTO_REISSUE rather than minting
			// one every five minutes until the morning.
			unclaimed += 1;
			if (secret.trim() && unclaimed <= MAX_AUTO_REISSUE) void issue();
		}
	}, 1000);

	/**
	 * Notice the scan.
	 *
	 * The claim happens over HTTP directly against jarvis-core, so nothing
	 * tells this page about it. Asking every two seconds while a code is live
	 * is what turns "single use" from a sentence in the copy into something the
	 * operator watches happen.
	 */
	const watcher = setInterval(() => {
		if (code && conn) void loadTokens(conn);
	}, 2000);

	onDestroy(() => {
		clearInterval(ticker);
		clearInterval(watcher);
	});
</script>

<section class="panel" data-testid="pairing">
	<div class="panel-head">
		<span>Pair a phone</span>
		{#if live}
			<span class="muted" data-testid="pair-expiry">{secondsLeft}s left</span>
		{/if}
	</div>

	<p class="muted">
		Scan this in the Jarvis app — PHONE → SCAN QR. The code is single-use and lasts five
		minutes; it is not a token, so a photograph of this screen is worthless once it expires.
		Enter the secret once and the console keeps a live code on screen by itself.
	</p>

	{#if claimedBy}
		<p class="ok" data-testid="pair-claimed" role="status">
			Paired <b>{claimedBy}</b>. That code is spent — press GENERATE for the next device.
		</p>
	{/if}

	<div class="row">
		<span class="name">
			<b>Pairing secret</b><span class="eid">JARVIS_PAIRING_SECRET</span>
		</span>
		<input
			type="password"
			aria-label="Pairing secret"
			data-testid="pair-secret"
			autocomplete="off"
			bind:value={secret}
			placeholder="set on the server"
		/>
	</div>
	<p class="muted small">
		Set where jarvis-core runs. Minting a code needs it as well as the console's own access,
		because anything that can reach this console can already use its token — so the token alone
		must not be enough to make a permanent one.
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
		<button
			type="button"
			class="btn"
			data-testid="pair-new"
			disabled={busy || !secret.trim()}
			onclick={issue}
		>
			{busy ? 'GENERATING…' : 'GENERATE CODE'}
		</button>
		{#if live}
			<button type="button" class="btn ghost" data-testid="pair-hide" onclick={forget}>
				HIDE
			</button>
		{/if}
	</div>
</section>

{#if tokensSupported}
	<!--
	  The other half of the door. Every credential the auth manager knows about,
	  built from IT rather than from any pairing record — a token store that
	  failed to load would otherwise render as "no devices" over a live
	  full-privilege credential, with no way to revoke it.
	-->
	<section class="panel" data-testid="tokens">
		<div class="panel-head">
			<span>What can reach this house</span>
			<span class="muted">{tokens.length} credential{tokens.length === 1 ? '' : 's'}</span>
		</div>
		{#if !tokens.length}
			<p class="muted" data-testid="tokens-empty">
				Nothing is stored. The server may be running on a token from its environment, which
				is not revocable from here — change it where jarvis-core runs.
			</p>
		{/if}
		{#each tokens as row (row.id)}
			<div class="row" data-testid="token-{row.id}">
				<span class="name">
					<b>{row.name}</b><span class="eid">{row.id}</span>
				</span>
				<span class="muted" data-testid="token-state-{row.id}">
					{row.connected ? 'connected now' : 'not connected'}
				</span>
				<button
					type="button"
					class="btn ghost danger"
					data-testid="token-revoke-{row.id}"
					disabled={revoking === row.id}
					onclick={() => revoke(row)}
				>
					REVOKE
				</button>
			</div>
		{/each}
		<p class="muted small">
			Revoking cuts a device off immediately, including any connection it already has open —
			otherwise "revoked" would mean "revoked the next time it reconnects", and a phone holds
			its connection for days.
		</p>
	</section>
{/if}

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
	.ok {
		color: var(--jv-ok, #4ade80);
	}
</style>
