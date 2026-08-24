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
	 *
	 * Minting is gated on the console password, not on the pairing secret typed
	 * out in full. The gate is checked server-side (`api/console/+server.ts`);
	 * this file only draws the two states, and the pairing secret does not reach
	 * this file at all until a session has been proved. See
	 * `$lib/server/consoleAuth` for what that keeps and what it changes.
	 */
	import { onDestroy, onMount } from 'svelte';
	import { qrSvg } from '$lib/qr';
	import { describeError, openConnection, type Connection } from '$lib/connection';
	import { toasts } from '$lib/toast';
	import type { AccessToken } from '$lib/jarvisClient';

	/** Which of the three doors the panel is drawing. */
	type Lock = 'loading' | 'choose' | 'locked' | 'open';
	let lock = $state<Lock>('loading');

	/**
	 * The console password, held in a variable for exactly as long as it takes
	 * to post it. Never in `sessionStorage`, never in `localStorage`: the
	 * session cookie the server sets is httpOnly, so what survives a reload is
	 * something page JavaScript — and therefore an XSS — cannot read back.
	 */
	let password = $state('');
	let passwordHint = $state('');
	let passwordFile = $state('');
	let passwordVar = $state('JARVIS_CONSOLE_PASSWORD');
	let minChars = $state(10);
	let unlocking = $state(false);

	/** Whether the SERVER holds a pairing secret. Never the secret itself. */
	let secretHeld = $state(false);
	let secretSource = $state<'env' | 'operator' | 'none'>('none');
	let secretVar = $state('JARVIS_PAIRING_SECRET');
	/** Typed once when this console has no secret of its own; sent, then cleared. */
	let secretDraft = $state('');
	/** The revealed secret, only ever assigned from a password-gated response. */
	let revealed = $state('');
	let revealing = $state(false);

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

	/**
	 * How many codes may expire unscanned before the console stops replacing
	 * them by itself. A tab left open on this page overnight should not mint a
	 * code every five minutes forever; three is enough to cover somebody
	 * walking to fetch the phone.
	 */
	const MAX_AUTO_REISSUE = 3;

	/**
	 * How long a revealed secret stays on screen. It is on a monitor now, which
	 * is the one place this design spent a whole module avoiding putting it —
	 * so it goes away by itself rather than waiting for somebody to remember.
	 */
	const REVEAL_MS = 60_000;
	let revealTimer: ReturnType<typeof setTimeout> | null = null;

	/** Seconds a code has left, or 0. Drives both the readout and the expiry. */
	const secondsLeft = $derived(expiresAt ? Math.max(0, Math.round(expiresAt - now / 1000)) : 0);
	const live = $derived(Boolean(code) && secondsLeft > 0);
	const canIssue = $derived(lock === 'open' && secretHeld);

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

	/** The server's own words for a refusal, which is what a person can act on. */
	async function failure(res: Response, fallback: string): Promise<string> {
		const detail = await res.json().catch(() => null);
		return detail?.message ?? `${fallback} (${res.status})`;
	}

	async function loadLock(): Promise<void> {
		const res = await fetch('/api/console');
		if (!res.ok) throw new Error(await failure(res, 'the console could not be read'));
		const body = await res.json();
		minChars = body.minChars ?? minChars;
		passwordVar = body.envVar ?? passwordVar;
		passwordFile = body.file ?? '';
		passwordHint = body.problem ?? '';
		lock = body.authenticated ? 'open' : body.configured ? 'locked' : 'choose';
	}

	/** Whether the server holds a secret — not what it is. */
	async function loadSecretStatus(): Promise<void> {
		const res = await fetch('/api/pair/secret');
		if (!res.ok) return;
		const body = await res.json();
		secretHeld = Boolean(body.held);
		secretSource = body.source ?? 'none';
		secretVar = body.envVar ?? secretVar;
	}

	/**
	 * Prove the password once, then every code after it is one press.
	 *
	 * On a console that has none yet this CHOOSES it — the only moment that can
	 * happen without one, and the alternative is a console nobody can lock
	 * without editing a file on the server.
	 */
	async function unlock(): Promise<void> {
		if (!password.trim() || unlocking) return;
		unlocking = true;
		err = '';
		try {
			const res = await fetch('/api/console', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ password })
			});
			if (!res.ok) throw new Error(await failure(res, 'the console refused that password'));
			const body = await res.json();
			// Out of the page's memory the moment it is spent. What survives is
			// the httpOnly cookie, which this code cannot read.
			password = '';
			lock = 'open';
			if (body.chosen) {
				toasts.success('Console password set', 'It is stored as a scrypt hash, not as itself.');
			}
			await loadSecretStatus();
			if (secretHeld) await issue();
		} catch (e) {
			err = describeError(e);
		} finally {
			unlocking = false;
		}
	}

	/** Lock it again — the operator walking away, not an expiry. */
	async function relock(): Promise<void> {
		await fetch('/api/console', { method: 'DELETE' }).catch(() => null);
		lock = 'locked';
		hideSecret();
		code = '';
		expiresAt = 0;
		unclaimed = 0;
	}

	/**
	 * Hand this console the pairing secret, once, for the life of the process.
	 *
	 * Only shown when the console holds none. It goes to the server and stays
	 * there in memory — the browser is not where it lives, which is why the
	 * `sessionStorage` copy this replaced had to go.
	 */
	async function adoptSecret(): Promise<void> {
		if (!secretDraft.trim() || busy) return;
		busy = true;
		err = '';
		try {
			const res = await fetch('/api/pair/secret', {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ secret: secretDraft })
			});
			if (!res.ok) throw new Error(await failure(res, 'the console would not take that secret'));
			secretDraft = '';
			await loadSecretStatus();
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = false;
		}
		if (secretHeld) await issue();
	}

	/** Read the secret back. The one thing the password is for besides minting. */
	async function reveal(): Promise<void> {
		if (revealing) return;
		revealing = true;
		err = '';
		try {
			const res = await fetch('/api/pair/secret', { method: 'POST' });
			if (!res.ok) {
				if (res.status === 401) lock = 'locked';
				throw new Error(await failure(res, 'the secret could not be read'));
			}
			const body = await res.json();
			revealed = body.secret ?? '';
			if (revealTimer) clearTimeout(revealTimer);
			revealTimer = setTimeout(hideSecret, REVEAL_MS);
		} catch (e) {
			err = describeError(e);
		} finally {
			revealing = false;
		}
	}

	function hideSecret(): void {
		revealed = '';
		if (revealTimer) clearTimeout(revealTimer);
		revealTimer = null;
	}

	/**
	 * Mint one. One click.
	 *
	 * No secret in the body any more: the server holds it and the session
	 * cookie is what releases it, so this posts nothing at all. That is the
	 * whole gain — the operator proves who they are once per browser session
	 * instead of retyping the secret for every device they ever add.
	 */
	async function issue(): Promise<void> {
		if (!canIssue || busy) return;
		busy = true;
		err = '';
		claimedBy = '';
		try {
			// The baseline goes FIRST. Whatever is on the house before the code
			// exists cannot be the phone that scans it, and anything that turns
			// up afterwards can. Taken after the mint instead, `known` was still
			// the empty set from before the page had read the token list at all,
			// so the very first code was declared claimed by an existing
			// credential the instant it was minted — the QR was replaced by
			// "Paired console" without anybody scanning anything.
			//
			// Re-read rather than trusted from mount, so a device paired from
			// another console in the meantime does not read as this code.
			if (conn) {
				await loadTokens(conn);
				known = new Set(tokens.map((t) => t.id));
			}
			const res = await fetch('/api/pair', { method: 'POST' });
			if (!res.ok) {
				// A session that expired mid-tab has to put the password form
				// back, not leave GENERATE failing at somebody.
				if (res.status === 401) lock = 'locked';
				throw new Error(await failure(res, 'pairing failed'));
			}
			const body = await res.json();
			code = body.code;
			expiresAt = body.expires_at ?? Date.now() / 1000 + (body.ttl ?? 300);
		} catch (e) {
			err = describeError(e);
			code = '';
			// jarvis-core may have refused the secret, in which case the server
			// has dropped it and the field for correcting it must come back.
			await loadSecretStatus();
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

	/**
	 * The countdown, and the watcher that notices a scan.
	 *
	 * Both are started in `onMount` and not in the instance body. A component's
	 * body runs on the SERVER too, so `setInterval` at the top level of this file
	 * armed two timers per render of /settings inside the node process — which
	 * never unmounts, so `onDestroy` never came, so they accumulated for the life
	 * of the server and ticked against a component nobody was looking at.
	 */
	let ticker: ReturnType<typeof setInterval> | null = null;
	let watcher: ReturnType<typeof setInterval> | null = null;

	onMount(() => {
		let disposed = false;
		ticker = setInterval(() => {
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
				if (canIssue && unclaimed <= MAX_AUTO_REISSUE) void issue();
			}
		}, 1000);
		// Notice the scan. The claim happens over HTTP directly against
		// jarvis-core, so nothing tells this page about it; asking every two
		// seconds while a code is live is what turns "single use" from a sentence
		// in the copy into something the operator watches happen.
		watcher = setInterval(() => {
			if (code && conn) void loadTokens(conn);
		}, 2000);
		(async () => {
			// The lock state first, and independently of the websocket: a
			// backend that is down must still show the password form rather
			// than an empty panel.
			try {
				await loadLock();
				await loadSecretStatus();
			} catch (e) {
				err = describeError(e);
				lock = 'locked';
			}
			try {
				const connection = await openConnection({});
				if (disposed) {
					connection.close();
					return;
				}
				conn = connection;
				await loadTokens(connection);
				// Already past the password from an earlier load in this browser
				// session: keep a live code on screen without a press.
				if (canIssue) await issue();
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

	onDestroy(() => {
		if (ticker) clearInterval(ticker);
		if (watcher) clearInterval(watcher);
		if (revealTimer) clearTimeout(revealTimer);
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
		Enter the console password once and every code after that is one press.
	</p>

	{#if lock === 'choose'}
		<!--
		  No password yet. This is the one moment it can be chosen from a
		  browser, and it has to be offered here: anything that reaches this
		  console can already use its admin token, so a console nobody has
		  locked is a console with no second factor at all.
		-->
		<div class="row" data-testid="pair-choose">
			<span class="name">
				<b>Choose a console password</b><span class="eid">{minChars} characters or more</span>
			</span>
			<input
				type="password"
				aria-label="Choose a console password"
				data-testid="pair-password"
				autocomplete="new-password"
				bind:value={password}
				onkeydown={(e) => e.key === 'Enter' && unlock()}
				placeholder="nobody has set one"
			/>
			<button
				type="button"
				class="btn"
				data-testid="pair-unlock"
				disabled={unlocking || password.trim().length < minChars}
				title={unlocking
					? 'Setting the password'
					: password.trim().length < minChars
						? `At least ${minChars} characters`
						: 'Set this console password'}
				onclick={unlock}
			>
				{unlocking ? 'SETTING…' : 'SET PASSWORD'}
			</button>
		</div>
		<p class="muted small">
			Stored as a scrypt hash in {passwordFile || '.storage/console-password'} — set
			{passwordVar} where the console runs to choose it there instead.
		</p>
	{:else if lock === 'locked'}
		<div class="row" data-testid="pair-lockform">
			<span class="name">
				<b>Console password</b><span class="eid">{passwordVar}</span>
			</span>
			<input
				type="password"
				aria-label="Console password"
				data-testid="pair-password"
				autocomplete="current-password"
				bind:value={password}
				onkeydown={(e) => e.key === 'Enter' && unlock()}
				placeholder="set where the console runs"
			/>
			<button
				type="button"
				class="btn"
				data-testid="pair-unlock"
				disabled={unlocking || !password.trim()}
				title={unlocking
					? 'Checking'
					: !password.trim()
						? 'Type the console password first'
						: 'Unlock pairing'}
				onclick={unlock}
			>
				{unlocking ? 'CHECKING…' : 'UNLOCK'}
			</button>
		</div>
		<p class="muted small">
			Minting a code needs it as well as the console's own access, because anything that can
			reach this console can already use its token — so the token alone must not be enough to
			make a permanent one.
		</p>
	{:else if lock === 'open'}
		<div class="row" data-testid="pair-unlocked">
			<span class="name">
				<b>Console unlocked</b><span class="eid">for this browser session</span>
			</span>
			<button type="button" class="btn ghost" data-testid="pair-relock" onclick={relock}>
				LOCK
			</button>
		</div>
	{/if}

	{#if lock === 'open' && !secretHeld}
		<!--
		  The console holds no pairing secret, so it cannot mint on the
		  operator's behalf. Typed once and kept in the SERVER's memory for the
		  life of the process — not in this tab, and not on disk beside the
		  admin token.
		-->
		<div class="row" data-testid="pair-secret-form">
			<span class="name">
				<b>Pairing secret</b><span class="eid">{secretVar}</span>
			</span>
			<input
				type="password"
				aria-label="Pairing secret"
				data-testid="pair-secret"
				autocomplete="off"
				bind:value={secretDraft}
				onkeydown={(e) => e.key === 'Enter' && adoptSecret()}
				placeholder="set where jarvis-core runs"
			/>
			<button
				type="button"
				class="btn"
				data-testid="pair-secret-save"
				disabled={busy || !secretDraft.trim()}
				onclick={adoptSecret}
			>
				HOLD IT
			</button>
		</div>
		<p class="muted small">
			jarvis-core prints it on first run. Set {secretVar} where this console runs and it survives
			a restart; given here it lives in the server's memory only.
		</p>
	{/if}

	{#if lock === 'open' && secretHeld}
		<div class="row" data-testid="pair-secret-row">
			<span class="name">
				<b>Pairing secret</b><span class="eid">
					{secretSource === 'env' ? secretVar : 'held for this process'}
				</span>
			</span>
			{#if revealed}
				<code class="small" data-testid="pair-secret-value">{revealed}</code>
				<button type="button" class="btn ghost" data-testid="pair-conceal" onclick={hideSecret}>
					HIDE
				</button>
			{:else}
				<button
					type="button"
					class="btn ghost"
					data-testid="pair-reveal"
					disabled={revealing}
					onclick={reveal}
				>
					{revealing ? 'READING…' : 'SHOW PAIRING SECRET'}
				</button>
			{/if}
		</div>
	{/if}

	{#if claimedBy}
		<p class="ok" data-testid="pair-claimed" role="status">
			Paired <b>{claimedBy}</b>. That code is spent — press GENERATE for the next device.
		</p>
	{/if}

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
	{#if passwordHint}<p class="err" data-testid="pair-password-problem">{passwordHint}</p>{/if}

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
			disabled={busy || !canIssue}
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
		width: min(var(--jv-measure-qr), 60vw);
		height: auto;
		/* The quiet zone is drawn by qrSvg; this only keeps the light modules
		   readable when the console is in its dark theme. */
		background: var(--jv-paper);
		border-radius: var(--jv-radius-sm);
	}
	.small {
		font-size: var(--jv-fs-xs);
		word-break: break-all;
	}
	.ok {
		color: var(--jv-ok);
	}
	code {
		/*
		 * A secret is read off the screen and typed elsewhere, so the glyphs have
		 * to be distinguishable — l/1 and O/0 in particular. That is what
		 * `--jv-font-chrome` is: a monospace stack. It used to ask for
		 * `--jv-font-mono`, which no file declares, so the one string on this
		 * console whose characters must not be guessable was rendered in the
		 * fallback — and the fallback was the body face.
		 */
		font-family: var(--jv-font-chrome);
		user-select: all;
	}
</style>
