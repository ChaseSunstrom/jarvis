import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

// Non-secret client configuration. The HA token never leaves the server.
export const GET: RequestHandler = () =>
	json({
		pipeline: env.JARVIS_PIPELINE || 'Jarvis',
		ttsVoice: env.JARVIS_TTS_VOICE || 'en_GB-alan-medium'
	});
