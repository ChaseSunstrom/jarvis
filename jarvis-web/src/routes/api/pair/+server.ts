import { error, json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend, backendProblem } from '$lib/server/backend';
import type { RequestHandler } from './$types';

// Mint a pairing code for the console to draw as a QR.
//
// Server-side because the admin token is server-side: the browser must never
// hold it, which is the rule the whole relay design exists to keep. The page
// asks here, this asks jarvis-core with the token, and only the CODE comes
// back — and a code is not a credential. See `jarvis-core/jarvis/api/pairing.py`
// for why the QR deliberately does not carry a token.
export const POST: RequestHandler = async ({ fetch }) => {
	const backend = resolveBackend(env);
	const problem = backendProblem(backend);
	if (problem) throw error(500, problem);

	const upstream = await fetch(`${backend.url.replace(/\/+$/, '')}/api/pair/new`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${backend.token}`, 'content-type': 'application/json' },
		body: '{}',
		redirect: 'error'
	}).catch(() => null);

	if (!upstream) throw error(502, 'the Jarvis server could not be reached');
	if (!upstream.ok) {
		// 404 means the backend predates pairing. Saying so beats a generic
		// failure on the one screen somebody uses while setting Jarvis up.
		throw error(
			upstream.status,
			upstream.status === 404
				? 'this Jarvis server has no pairing endpoint — update it'
				: `the Jarvis server answered ${upstream.status}`
		);
	}
	const body = await upstream.json().catch(() => null);
	if (!body?.code) throw error(502, 'the Jarvis server returned no pairing code');
	// Only the code and its clock. Nothing else from upstream is forwarded, so
	// a future field on that response cannot leak through this hop by accident.
	return json({ code: body.code, expires_at: body.expires_at, ttl: body.ttl });
};
