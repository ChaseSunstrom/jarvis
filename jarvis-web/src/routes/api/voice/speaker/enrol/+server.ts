import { relaySpeakerWrite } from '$lib/server/speakerRelay';
import type { RequestHandler } from './$types';

// Add one voice sample to the owner's profile.
//
// The phone's microphone, relayed to jarvis-core under the PHONE's token — see
// `$lib/server/speakerRelay`. This route deliberately never touches the
// server-held admin token: enrolling changes whose voice Jarvis answers, and
// doing that on the strength of "you could reach this port" would be the
// opposite of the feature.
export const POST: RequestHandler = async ({ request }) =>
	relaySpeakerWrite(request, globalThis.fetch, 'enrol');
