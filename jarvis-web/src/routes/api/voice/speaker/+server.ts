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
// ENROL is the sibling route, `enrol/+server.ts`, and carries the caller's own
// credential rather than the admin token; see `$lib/server/speakerRelay`.
//
// One thing from the caller reaches the upstream URL: `label`, the name of
// one enrolled person (M71) — `GET ?label=` describes one person and `DELETE
// ?label=` forgets one rather than everyone. It is checked here to the same
// rule jarvis-core applies (`normalise_label`: printable, at most forty
// characters) and then URL-encoded, so what is appended is a name and never
// a path, a second query, or a control character bound for a log line.

const MAX_LABEL_CHARS = 40;

function upstreamUrl(base: string, label: string | null): string {
	const root = `${base.replace(/\/+$/, '')}/api/voice/speaker`;
	return label === null ? root : `${root}?label=${encodeURIComponent(label)}`;
}

/** The `label` query, cleaned the way the server cleans it, or null for "everyone". */
function labelOf(url: URL): string | null {
	const raw = url.searchParams.get('label');
	if (raw === null) return null;
	const label = raw.split(/\s+/).filter(Boolean).join(' ');
	if (!label) return null;
	if (label.length > MAX_LABEL_CHARS) {
		throw error(400, `a name is at most ${MAX_LABEL_CHARS} characters`);
	}
	if (/[\u0000-\u001f\u007f]/.test(label)) throw error(400, 'a name may not contain control characters');
	return label;
}

export const GET: RequestHandler = async ({ url }) => {
	const fetch = globalThis.fetch;
	const backend = resolveBackend(env);
	const problem = backendProblem(backend);
	if (problem) throw error(500, problem);

	const upstream = await fetch(upstreamUrl(backend.url, labelOf(url)), {
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

export const DELETE: RequestHandler = async ({ cookies, url }) => {
	const fetch = globalThis.fetch;
	const backend = resolveBackend(env);
	const problem = backendProblem(backend);
	if (problem) throw error(500, problem);

	if (!sessionValid(cookies.get(SESSION_COOKIE))) {
		throw error(401, 'unlock the console with its password first');
	}

	const upstream = await fetch(upstreamUrl(backend.url, labelOf(url)), {
		method: 'DELETE',
		headers: { Authorization: `Bearer ${backend.token}` },
		redirect: 'error'
	}).catch(() => null);

	if (!upstream) throw error(502, 'the Jarvis server could not be reached');
	if (!upstream.ok) {
		// jarvis-core's own words for a 404 — "'Ted' is not enrolled" — are
		// the useful part; the code alone is not.
		const detail = await upstream
			.json()
			.then((body) => (body && typeof body.detail === 'string' ? body.detail : ''))
			.catch(() => '');
		throw error(upstream.status, detail || `the Jarvis server answered ${upstream.status}`);
	}
	return json(await upstream.json().catch(() => ({ enrolled: false })));
};
