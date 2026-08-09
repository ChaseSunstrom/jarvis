import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend, backendProblem } from '$lib/server/backend';
import type { RequestHandler } from './$types';

// Non-secret client configuration. The backend token never leaves the server —
// the settings page reports only whether one is configured.
export const GET: RequestHandler = () => {
	const backend = resolveBackend(env);
	return json({
		pipeline: env.JARVIS_PIPELINE || 'Jarvis',
		ttsVoice: env.JARVIS_TTS_VOICE || 'en_GB-alan-medium',
		backend: backend.kind,
		backendUrl: backend.url,
		backendUrlVar: backend.source.url,
		backendTokenVar: backend.source.token,
		tokenConfigured: Boolean(backend.token),
		problem: backendProblem(backend)
	});
};
