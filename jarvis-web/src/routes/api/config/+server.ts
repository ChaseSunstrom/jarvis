import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend, backendProblem } from '$lib/server/backend';
import { clampHangover } from '$lib/server/voiceTuning';
import type { RequestHandler } from './$types';

// Non-secret client configuration. The backend token never leaves the server —
// the settings page reports only whether one is configured.
export const GET: RequestHandler = () => {
	const backend = resolveBackend(env);
	return json({
		pipeline: env.JARVIS_PIPELINE || 'Jarvis',
		ttsVoice: env.JARVIS_TTS_VOICE || 'en_GB-alan-medium',
		// How long a pause must last before a spoken turn is considered over.
		// The single biggest piece of dead air in a turn, and the one thing that
		// genuinely differs between rooms — so it is tunable without a rebuild.
		// Clamped here rather than in the browser: a typo of `50` would end
		// every turn on the first comma, and a typo of `50000` would never end
		// one at all.
		hangoverMs: clampHangover(env.JARVIS_VAD_HANGOVER_MS),
		backend: backend.kind,
		backendUrl: backend.url,
		backendUrlVar: backend.source.url,
		backendTokenVar: backend.source.token,
		tokenConfigured: Boolean(backend.token),
		problem: backendProblem(backend)
	});
};
