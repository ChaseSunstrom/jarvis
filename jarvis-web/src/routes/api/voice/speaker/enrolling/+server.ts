// M79 — "recording now". The console tells jarvis-core an enrolment phrase is
// about to be recorded, so the house's listeners do not treat the phrase as a
// command for the next twenty seconds (a sample refreshes the mark). The same
// relay as enrol: authorised by what the caller holds, never by reachability.
import { relaySpeakerWrite } from '$lib/server/speakerRelay';
import { SESSION_COOKIE, sessionValid } from '$lib/server/consoleAuth';
import type { RequestHandler } from './$types';

// Add one voice sample to the owner's profile.
//
// TWO callers, two credentials, one rule. The rule is that enrolment is
// authorised by something the caller HOLDS — never by the fact that they could
// reach this port, because teaching Jarvis a new owner on the strength of
// reachability is the opposite of what the feature is for.
//
//   the phone    its own Jarvis token, relayed unchanged. jarvis-core stays the
//                single authority on tokens and this stays a pipe.
//   the console  the console password, which is already the door in front of
//                the pairing secret and in front of DELETE on this same
//                profile. Enrolling and deleting both change whose voice
//                Jarvis answers.
//
// The console used to be refused here, and the comment on the sibling route
// gave two reasons: the phone has the microphone, and a second enrolment
// surface is a second place for the prompt list to drift. The first is a
// preference. The second stopped being true — the phrases live in jarvis-core
// and arrive in the status payload, so both surfaces read the same list from
// the same place and cannot drift.
export const POST: RequestHandler = async ({ request, cookies }) =>
	relaySpeakerWrite(
		request,
		globalThis.fetch,
		'enrolling',
		sessionValid(cookies.get(SESSION_COOKIE))
	);
