import { relaySpeakerWrite } from '$lib/server/speakerRelay';
import type { RequestHandler } from './$types';

// Score a sample WITHOUT enrolling it — the enrolment screen's TEST button.
//
// Same door as `enrol`: the caller's own token, never the admin one. It changes
// nothing, but it does report how close a voice is to the owner's, which is not
// something to hand out to whoever can reach this port.
export const POST: RequestHandler = async ({ request, fetch }) =>
	relaySpeakerWrite(request, fetch, 'verify');
