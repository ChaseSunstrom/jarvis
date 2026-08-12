import { error, json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend, backendProblem } from '$lib/server/backend';
import type { RequestHandler } from './$types';

/**
 * The other half of pairing, and the half that was missing.
 *
 * ## Why this route has to exist
 *
 * Reported as *"when scanning the QR code, it says 'that url has no endpoint'"*.
 *
 * The QR carries an ADDRESS and a code, and the address defaults to the origin
 * the pairing panel is being served from — `Pairing.svelte`: `if (!url) url =
 * location.origin`. That default is right: the console is the machine with a
 * web page on it, so it is the address somebody has actually typed and
 * demonstrably one that reaches Jarvis from this network.
 *
 * The phone then does `POST <url>/api/pair/claim` (`PairingClaim.kt`), which
 * existed only on jarvis-core. Against a console, 404 — rendered by the app as
 * "That server has no pairing endpoint. Update Jarvis on your server", sending
 * people to look at their jarvis-core version over a route that was never
 * there.
 *
 * This is the third instance of one gap: `/api/voice/speaker/enrol` and
 * `/api/voice/speaker/verify` were the first two, reported the same week as
 * "Could not reach Jarvis". The app has a whole `ServerKind.RELAY` for being
 * pointed at this console, and every jarvis-core path it calls has to answer
 * here too. `android-app/tools/api_parity_test.py` now fails the build for one
 * that does not.
 *
 * ## What it does NOT do
 *
 * **It attaches nothing.** No admin token, no header of its own. This is the
 * one unauthenticated write in the whole API and it has to be — the phone has
 * no credential yet, which is the entire problem being solved. What makes it
 * safe lives on the other side: a 192-bit code that lives five minutes, is
 * single-use, is compared in constant time, and stops being answerable after
 * ten failures. Adding authority here would not help and could only hurt.
 *
 * **It does not forward a client address.** jarvis-core buckets the claim's
 * rate limit by `request.client.host` and reads no forwarded header, so one
 * set here would be a header nothing reads — and the version of jarvis-core
 * that did read it would be trusting a value this hop's own callers can set,
 * which turns a ten-guess limit into an unlimited one. The cost is that claims
 * arriving through the console share a single bucket, so ten bad codes from
 * one handset pause pairing for everyone until the window rolls. That is an
 * annoyance; the alternative is a defeated limit. jarvis-core already treats
 * the bucket as best-effort for the same reason: "Spoofing it buys a fresh
 * allowance and nothing else — the code's entropy is what the security rests
 * on."
 *
 * **It does not launder away the browser guard.** jarvis-core refuses a claim
 * that arrives with an `Origin` header, in its own words: "A browser may not
 * claim. Browsers always send `Origin` on a cross-origin POST and phones never
 * do, so this costs the real client nothing and takes the hostile-web-page
 * attacker off the one unauthenticated write here."
 *
 * The header does not survive the hop by itself, so the guard is re-applied
 * HERE, against the request this route received, before anything is forwarded.
 * Otherwise the console would be the softer of the two front doors, which is
 * the opposite of what a relay is allowed to be.
 *
 * **It uses the PLATFORM fetch, not SvelteKit's `event.fetch`.** This one cost
 * a round trip to find and it is the reason the first version of this file did
 * not work. `event.fetch` stamps an Origin on anything that does not already
 * have one::
 *
 *     // @sveltejs/kit/src/runtime/server/fetch.js
 *     if (!request.headers.has('origin')) {
 *       request.headers.set('origin', event.url.origin);
 *     }
 *
 * and only removes it again for GET and HEAD. So the relay's POST arrived at
 * jarvis-core wearing the console's origin, jarvis-core's browser guard fired
 * exactly as designed, and the phone was told *"Pairing codes are claimed by
 * the app, not from a browser"* — a true sentence about a request no browser
 * had made. The console is not a page making a cross-origin request; it is a
 * server relaying one, and `event.fetch` cannot represent that. Every proxy in
 * this directory now uses the platform fetch for the same reason, before the
 * next guard jarvis-core grows finds them one at a time.
 */
export const POST: RequestHandler = async ({ request }) => {
	const backend = resolveBackend(env);
	const problem = backendProblem(backend);
	if (problem) throw error(500, problem);

	// jarvis-core's guard, re-applied to the request IT will never see. Same
	// status and same sentence, so the app's own error handling cannot tell
	// which of the two refused it.
	if (request.headers.get('origin')) {
		throw error(403, 'Pairing codes are claimed by the app, not from a browser.');
	}

	const body = await request.text();
	if (!body) throw error(400, 'no pairing code in the request');

	const upstream = await fetch(`${backend.url.replace(/\/+$/, '')}/api/pair/claim`, {
		method: 'POST',
		headers: { 'Content-Type': request.headers.get('content-type') ?? 'application/json' },
		body,
		// The ANSWER to this request is a permanent token. A 30x must not be
		// able to decide who supplies it. Same guard as every other proxy here.
		redirect: 'error'
	}).catch(() => null);

	if (!upstream) throw error(502, 'the Jarvis server could not be reached');

	// jarvis-core's own status and body, unread and unlogged. Its `detail` for a
	// spent or expired code is written for somebody to act on ("show a new
	// one"), and the success body carries a credential — so this hop neither
	// rewrites it nor records it.
	const text = await upstream.text();
	return new Response(text, {
		status: upstream.status,
		headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' }
	});
};

/** Nothing else lives at this path; say so rather than rendering the app shell. */
export const GET: RequestHandler = () => json({ detail: 'POST a pairing code here' }, { status: 405 });
