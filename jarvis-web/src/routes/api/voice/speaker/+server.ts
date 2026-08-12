import { error, json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend, backendProblem } from '$lib/server/backend';
import { SESSION_COOKIE, sessionValid } from '$lib/server/consoleAuth';
import type { RequestHandler } from './$types';

// The console's view of whose voice Jarvis answers.
//
// Server-side because the admin token is server-side — the browser must never
// hold it, which is the rule the whole relay design exists to keep.
//
// **Read is open, delete is behind the console password**, and the asymmetry is
// deliberate. The status payload is counts, scores and timestamps: it says
// somebody is enrolled and how well their samples agree, and it deliberately
// never carries the voiceprint itself, so it is no more sensitive than the rest
// of this page. Deleting is different — it disables the gate, and this relay
// attaches the admin token to whatever asks it, so a script with transient
// reach to this port would otherwise be able to turn off the thing that refuses
// strangers. That is exactly the reasoning `api/pair/+server.ts` gives for
// putting minting behind the same door.
//
// There is no ENROL here on purpose. Enrolment needs a microphone and five
// spoken phrases; the browser could do it, but the phone is where the person
// and the microphone already are, and a second enrolment surface is a second
// place for the prompt list to drift. The console shows the state and can
// clear it — see `docs/voice-identity.md`.

function upstreamUrl(base: string): string {
	return `${base.replace(/\/+$/, '')}/api/voice/speaker`;
}

export const GET: RequestHandler = async () => {
	const fetch = globalThis.fetch;
	const backend = resolveBackend(env);
	const problem = backendProblem(backend);
	if (problem) throw error(500, problem);

	const upstream = await fetch(upstreamUrl(backend.url), {
		headers: { Authorization: `Bearer ${backend.token}` },
		// A 30x must not move this request — or the admin token — to another
		// host. Same guard as every other proxy in this directory.
		redirect: 'error'
	}).catch(() => null);

	if (!upstream) throw error(502, 'the Jarvis server could not be reached');
	if (upstream.status === 404) {
		// A backend that predates voice identity. Saying which beats a generic
		// failure, because the fix is "update jarvis-core" rather than anything
		// on this page.
		return json({ supported: false, enrolled: false, mode: 'off', active: false });
	}
	if (!upstream.ok) throw error(upstream.status, `the Jarvis server answered ${upstream.status}`);

	const payload = await upstream.json().catch(() => null);
	if (!payload || typeof payload !== 'object') {
		throw error(502, 'the Jarvis server sent an unreadable answer');
	}
	return json({ supported: true, ...payload });
};

export const DELETE: RequestHandler = async ({ cookies }) => {
	const fetch = globalThis.fetch;
	const backend = resolveBackend(env);
	const problem = backendProblem(backend);
	if (problem) throw error(500, problem);

	if (!sessionValid(cookies.get(SESSION_COOKIE))) {
		throw error(401, 'unlock the console with its password first');
	}

	const upstream = await fetch(upstreamUrl(backend.url), {
		method: 'DELETE',
		headers: { Authorization: `Bearer ${backend.token}` },
		redirect: 'error'
	}).catch(() => null);

	if (!upstream) throw error(502, 'the Jarvis server could not be reached');
	if (!upstream.ok) throw error(upstream.status, `the Jarvis server answered ${upstream.status}`);
	return json(await upstream.json().catch(() => ({ enrolled: false })));
};
