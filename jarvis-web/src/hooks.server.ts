import type { Handle } from '@sveltejs/kit';

/**
 * The User-Agent `ManagementActivity` gives its WebView.
 *
 * Kept in step with the Kotlin by `console_parity_test.py`, which reads both:
 * change the agent and nothing here fails, the duplicate nav just comes back.
 */
const ANDROID_FRAME = /JarvisAndroid/;

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
	// Tell the page whether the Android app's console frame is around it, so it
	// can stop drawing the chrome that frame already draws — see the
	// `data-embed` rules in `src/routes/+layout.svelte`.
	//
	// Server-side, from the request header, rather than an inline script
	// sniffing `navigator.userAgent`: this app's CSP is `script-src: 'self'`
	// with no unsafe-inline, so such a script is BLOCKED — silently, since a
	// refused inline script leaves the page working and only the marker
	// missing. Doing it here also means the attribute is on the very first
	// byte, so the duplicate nav is never painted and then yanked.
	const embedded = ANDROID_FRAME.test(event.request.headers.get('user-agent') ?? '');
	return resolve(event, {
		transformPageChunk: ({ html }) =>
			html.replace('%jarvis.embed%', embedded ? 'android' : '')
	});
};
