import { error } from '@sveltejs/kit';
import { resolveBackend, backendProblem } from './backend';

/**
 * Relaying a voice-identity WRITE from the phone to jarvis-core.
 *
 * ## Why these two are different from the rest of the console's proxies
 *
 * Every other server route here attaches the **server-held admin token**, and
 * `routes.test.ts` makes each of them say what stops that being a way to reach
 * the backend with it. These two attach nothing. They forward the caller's own
 * `Authorization` header and let jarvis-core decide.
 *
 * That is deliberate, and it is the same rule `server/ws-proxy.js` already
 * applies to the phone's WebSocket, in its words: *"The relay does NOT inject
 * anything for it: the token is passed straight through and jarvis-core decides
 * whether it is any good. jarvis-core stays the single authority on tokens, and
 * this stays a pipe."*
 *
 * Enrolment is a **write that changes whose voice Jarvis answers**. Relaying it
 * under the admin token would mean anyone who could reach this port could teach
 * Jarvis a new owner — which is the opposite of what the feature is for. The
 * read (`GET /api/voice/speaker`) can be open because it returns counts and
 * scores; deleting needs the console password because it disables the gate.
 * Enrolling needs to be *the phone's own credential*, because the phone is the
 * only thing that has one and the person holding it is the point.
 *
 * ## Why these routes exist at all
 *
 * Reported as *"with the teach voice thing, it says 'Could not reach Jarvis'"*.
 * A phone can be paired to jarvis-core directly OR to this console — the app
 * has a whole `ServerKind` for it, and the console is the address people
 * actually type, because it is the one with a web page on it. Pointed here, the
 * enrolment screen asked for `/api/voice/speaker/enrol`, which existed only on
 * jarvis-core. 404, on a server that was answering perfectly well.
 */
export async function relaySpeakerWrite(
	request: Request,
	fetcher: typeof globalThis.fetch,
	path: 'enrol' | 'verify'
): Promise<Response> {
	const backend = resolveBackend(process.env);
	const problem = backendProblem(backend);
	if (problem) throw error(500, problem);

	// The caller's own credential, or nothing. No fallback to the admin token:
	// a missing header here must fail, not quietly succeed with more authority
	// than the caller had.
	const presented = request.headers.get('authorization');
	if (!presented || !/^Bearer\s+\S/i.test(presented)) {
		throw error(401, 'enrolling a voice needs the phone’s own Jarvis token');
	}

	const body = await request.arrayBuffer();
	if (body.byteLength === 0) throw error(400, 'no audio in the request');

	const upstream = await fetcher(`${backend.url.replace(/\/+$/, '')}/api/voice/speaker/${path}`, {
		method: 'POST',
		headers: {
			Authorization: presented,
			'Content-Type': request.headers.get('content-type') ?? 'application/octet-stream'
		},
		body,
		// A 30x must not move this request — or the caller's token — to another
		// host. Same guard as every other proxy in this directory.
		redirect: 'error'
	}).catch(() => null);

	if (!upstream) throw error(502, 'the Jarvis server could not be reached');

	// jarvis-core's own `detail` is written for a person to act on ("that sample
	// has no measurable pitch — it is too quiet"), and the phone shows it. Pass
	// the status and the body through rather than flattening them.
	const text = await upstream.text();
	return new Response(text, {
		status: upstream.status,
		headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' }
	});
}
