import { error } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

// Proxy TTS audio from Home Assistant, attaching the server-held token.
// Only HA TTS media paths are allowed (SSRF guard).
const ALLOWED_PREFIXES = ['/api/tts_proxy/', '/api/tts/'];

export const GET: RequestHandler = async ({ url }) => {
	const path = url.searchParams.get('path') ?? '';
	const allowed =
		ALLOWED_PREFIXES.some((p) => path.startsWith(p)) &&
		!path.includes('..') &&
		!path.includes('//') &&
		!path.includes('\\');
	if (!allowed) throw error(400, 'invalid tts path');

	const haUrl = (env.HA_URL ?? '').replace(/\/+$/, '');
	if (!haUrl || !env.HA_TOKEN) throw error(500, 'server missing HA_URL/HA_TOKEN');

	const upstream = await fetch(`${haUrl}${path}`, {
		headers: { Authorization: `Bearer ${env.HA_TOKEN}` }
	});
	if (!upstream.ok || !upstream.body) throw error(502, `upstream tts error (${upstream.status})`);

	return new Response(upstream.body, {
		headers: {
			'content-type': upstream.headers.get('content-type') ?? 'audio/mpeg',
			'cache-control': 'no-store'
		}
	});
};
