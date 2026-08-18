import { relaySpeakerWrite } from '$lib/server/speakerRelay';
import { SESSION_COOKIE, sessionValid } from '$lib/server/consoleAuth';
import type { RequestHandler } from './$types';

// Score a sample WITHOUT enrolling it — the enrolment screen's TEST button, and
// the only honest way to find a threshold before enforcing one.
//
// Same two credentials as `enrol`, for the same reason and with the same
// asymmetry: it changes nothing, but it does report how close a given voice is
// to the owner's, which is not something to hand to whoever can reach this
// port. A console session is a credential; reachability is not.
export const POST: RequestHandler = async ({ request, cookies }) =>
	relaySpeakerWrite(
		request,
		globalThis.fetch,
		'verify',
		sessionValid(cookies.get(SESSION_COOKIE))
	);
