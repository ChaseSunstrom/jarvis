import { error } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend, modelProxyTarget } from '$lib/server/backend';
import type { RequestHandler } from './$types';

// Wake-word weights, on their way to a phone.
//
// jarvis-core mirrors the openWakeWord models so the app never talks to GitHub
// — see `jarvis-core/jarvis/api/models.py` for why that is worth a route. The
// app asks for `/api/models/<name>` on whatever URL it was given, and the URL
// people give it is this one: the console is the server with a web page on it.
// Without this handler that request was a 404 and the on-device wake word could
// never be set up through the console at all.
//
// **The admin token is deliberately not attached here.** The TTS proxy next
// door does attach it, because a browser cannot hold one; this caller is the
// Android app, which already has its own token, and passing that through means
// jarvis-core does the authorising exactly as it would if the phone had dialled
// it directly. An unauthenticated request is refused here rather than upstream,
// so this route cannot become a way to reach the backend without a token.
export const GET: RequestHandler = async ({ params, request }) => {
	const fetch = globalThis.fetch;
	const authorization = request.headers.get('authorization');
	if (!authorization) throw error(401, 'a bearer token is required for model downloads');

	const backend = resolveBackend(env);
	// Only the URL, not the token: this route never uses the server-held one, so
	// a console configured with an address and no admin token can still put the
	// models on a phone that brought its own.
	if (!backend.url) throw error(500, `server missing ${backend.source.url}`);

	const target = modelProxyTarget(params.name, backend.url);
	if (!target) throw error(400, 'invalid model name');

	// `redirect: 'error'` so a 30x from the backend cannot move the download —
	// or the caller's token — onto another host.
	const upstream = await fetch(target, {
		headers: { Authorization: authorization },
		redirect: 'error'
	}).catch(() => null);
	if (!upstream) throw error(502, 'the Jarvis server could not be reached for models');
	if (!upstream.ok || !upstream.body) {
		// Pass the status through rather than flattening it: the app tells 401
		// ("check the token"), 404 ("this server has no model mirror — update
		// it") and 502 ("it could not fetch them") apart, and says so.
		throw error(upstream.status, `the Jarvis server answered ${upstream.status} for models`);
	}

	const headers: Record<string, string> = {
		'content-type': upstream.headers.get('content-type') ?? 'application/octet-stream',
		'cache-control': 'no-store'
	};
	// The digest travels with the bytes so the phone verifies what it received
	// rather than trusting the transfer — dropping it here would silently turn
	// the check off at exactly the hop that added a middlebox.
	const digest = upstream.headers.get('x-jarvis-sha256');
	if (digest) headers['x-jarvis-sha256'] = digest;
	const length = upstream.headers.get('content-length');
	if (length) headers['content-length'] = length;

	return new Response(upstream.body, { headers });
};
