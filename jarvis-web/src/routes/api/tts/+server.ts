import { error } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend, backendProblem, mediaProxyTarget } from '$lib/server/backend';
import type { RequestHandler } from './$types';

// Proxy TTS audio from the selected backend (jarvis-core or Home Assistant),
// attaching the server-held token. Both serve synthesised speech under
// /api/tts_proxy/.
//
// The allow-list lives in mediaProxyTarget(), which validates the *normalised*
// URL — a plain `path.includes('..')` test is bypassable with `%2e%2e`, and the
// token this handler attaches is an admin token, so a bypass reads every REST
// endpoint the backend has.
export const GET: RequestHandler = async ({ url }) => {
	const path = url.searchParams.get('path') ?? '';

	const backend = resolveBackend(env);
	const problem = backendProblem(backend);
	if (problem) throw error(500, problem);

	const target = mediaProxyTarget(path, backend.url);
	if (!target) throw error(400, 'invalid tts path');

	// `redirect: 'error'` keeps a 30x from the backend from pivoting the
	// tokenless-to-the-browser proxy onto some other host.
	const upstream = await fetch(target, {
		headers: { Authorization: `Bearer ${backend.token}` },
		redirect: 'error'
	}).catch(() => null);
	if (!upstream) throw error(502, 'upstream tts error (unreachable)');
	if (!upstream.ok || !upstream.body) throw error(502, `upstream tts error (${upstream.status})`);

	return new Response(upstream.body, {
		headers: {
			'content-type': upstream.headers.get('content-type') ?? 'audio/mpeg',
			'cache-control': 'no-store'
		}
	});
};
