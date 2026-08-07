import type { Handle } from '@sveltejs/kit';

// Session id: random, httpOnly (never readable by page JS, never in
// localStorage). Identifies the browser session to the server only.
export const handle: Handle = async ({ event, resolve }) => {
	if (!event.cookies.get('jarvis_sid')) {
		event.cookies.set('jarvis_sid', crypto.randomUUID(), {
			path: '/',
			httpOnly: true,
			sameSite: 'lax',
			secure: event.url.protocol === 'https:',
			maxAge: 60 * 60 * 24 * 30
		});
	}
	return resolve(event);
};
