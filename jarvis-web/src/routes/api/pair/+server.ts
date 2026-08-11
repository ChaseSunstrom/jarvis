import { error, json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend, backendProblem } from '$lib/server/backend';
import {
	ENV_PAIRING_SECRET,
	SESSION_COOKIE,
	dropPairingSecret,
	pairingSecret,
	sessionValid
} from '$lib/server/consoleAuth';
import type { RequestHandler } from './$types';

// Mint a pairing code for the console to draw as a QR.
//
// Server-side because the admin token is server-side: the browser must never
// hold it, which is the rule the whole relay design exists to keep. The page
// asks here, this asks jarvis-core with the token, and only the CODE comes
// back — and a code is not a credential. See `jarvis-core/jarvis/api/pairing.py`
// for why the QR deliberately does not carry a token.
export const POST: RequestHandler = async ({ cookies, fetch }) => {
	const backend = resolveBackend(env);
	const problem = backendProblem(backend);
	if (problem) throw error(500, problem);

	// The console password, proved server-side, is the second factor. It has to
	// be one, because this relay attaches the admin token to whatever asks and
	// its `/ws` origin guard deliberately admits a client that sends no Origin —
	// so a script with transient reach to this port is already an authenticated
	// API client. Without something it does not have, it could mint a code,
	// claim it, and keep a permanent token: reach for a minute becomes access
	// forever. The pairing secret used to be that something and was typed in
	// full every time; now it is held here and the password is what releases it.
	if (!sessionValid(cookies.get(SESSION_COOKIE))) {
		throw error(401, 'unlock the console with its password first');
	}

	const held = pairingSecret(env);
	if (!held.secret) {
		throw error(
			400,
			`this console holds no pairing secret — set ${ENV_PAIRING_SECRET} where it runs, or give it one in the pairing panel`
		);
	}

	const upstream = await fetch(`${backend.url.replace(/\/+$/, '')}/api/pair/new`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${backend.token}`, 'content-type': 'application/json' },
		body: JSON.stringify({ secret: held.secret }),
		redirect: 'error'
	}).catch(() => null);

	if (!upstream) throw error(502, 'the Jarvis server could not be reached');
	if (!upstream.ok) {
		// A secret jarvis-core refuses is not worth holding: keeping it would
		// leave GENERATE failing forever with no field on screen to correct it,
		// because the panel hides that field exactly when a secret is held.
		// Only the operator's copy is dropped — one from the environment is the
		// console's configuration, and a browser request must not clear it.
		if (upstream.status === 403 && held.source === 'operator') dropPairingSecret();
		// 404 means the backend predates pairing, and saying so beats a generic
		// failure on the one screen somebody uses while setting Jarvis up.
		// Otherwise the backend's own words, which for a refused secret is the
		// thing somebody at the keyboard can act on.
		const detail = await upstream.json().catch(() => null);
		throw error(
			upstream.status,
			upstream.status === 404
				? 'this Jarvis server has no pairing endpoint — update it'
				: (detail?.detail ?? `the Jarvis server answered ${upstream.status}`)
		);
	}
	const body = await upstream.json().catch(() => null);
	if (!body?.code) throw error(502, 'the Jarvis server returned no pairing code');
	// Only the code and its clock. Nothing else from upstream is forwarded, so
	// a future field on that response cannot leak through this hop by accident.
	return json({ code: body.code, expires_at: body.expires_at, ttl: body.ttl });
};
