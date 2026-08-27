import { error } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend } from '$lib/server/backend';
import type { RequestHandler } from './$types';

// "You can leave with your data" means a file, and a browser cannot hold the
// admin token — so the download comes through here, exactly as the TTS proxy
// does, and the token stays on the server.
//
// Read-only and one path: this route can fetch `/api/memory/export` from the
// backend and nothing else. A general "proxy anything under /api" handler
// would hand the browser the whole authenticated surface, which is the thing
// the token being server-side is for.
export const GET: RequestHandler = async ({ url }) => {
	const backend = resolveBackend(env);
	if (!backend.url) throw error(500, `server missing ${backend.source.url}`);
	if (!backend.token) throw error(500, `server missing ${backend.source.token}`);

	const format = url.searchParams.get('format') === 'markdown' ? 'markdown' : 'json';
	const target = `${backend.url.replace(/\/+$/, '')}/api/memory/export?format=${format}`;
	const upstream = await globalThis
		.fetch(target, {
			headers: { Authorization: `Bearer ${backend.token}` },
			redirect: 'error'
		})
		.catch(() => null);
	if (!upstream || !upstream.ok) throw error(502, 'the backend would not export the memory');

	const body = await upstream.text();
	const isMarkdown = format === 'markdown';
	return new Response(body, {
		status: 200,
		headers: {
			'content-type': isMarkdown
				? 'text/markdown; charset=utf-8'
				: 'application/json; charset=utf-8',
			// Named, so it lands in Downloads as something recognisable rather
			// than as "export".
			'content-disposition': `attachment; filename="jarvis-memory.${isMarkdown ? 'md' : 'json'}"`
		}
	});
};
