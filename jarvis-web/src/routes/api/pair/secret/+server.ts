import { error, json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import {
	ENV_PAIRING_SECRET,
	SESSION_COOKIE,
	dropPairingSecret,
	holdPairingSecret,
	pairingSecret,
	sessionValid
} from '$lib/server/consoleAuth';
import type { RequestHandler } from './$types';

// The pairing secret this console holds, and the one door to reading it back.
//
// The secret is what stops reach to this port being enough to mint a permanent
// credential (`jarvis-core/jarvis/api/pairing.py`). So the reveal is a POST
// behind a proved session, and the value is not in the page's state before
// then — a control that fetched the secret on load and hid it behind a button
// would be a control that has already handed it over.

function unlocked(cookies: { get(name: string): string | undefined }): boolean {
	return sessionValid(cookies.get(SESSION_COOKIE));
}

/**
 * Whether this console holds one at all. No session required and no value
 * returned: the panel has to know which of two things to draw before anybody
 * has typed anything, and "a pairing secret is configured" is the same class of
 * fact as `/api/config`'s `tokenConfigured`.
 */
export const GET: RequestHandler = () => {
	const held = pairingSecret(env);
	return json({ held: Boolean(held.secret), source: held.source, envVar: ENV_PAIRING_SECRET });
};

/** Read it back. The whole point of the password. */
export const POST: RequestHandler = ({ cookies }) => {
	if (!unlocked(cookies)) throw error(401, 'unlock the console first');
	const held = pairingSecret(env);
	if (!held.secret) throw error(404, 'this console holds no pairing secret');
	return json({ secret: held.secret, source: held.source });
};

/**
 * Hand this console the secret for the life of the process.
 *
 * Memory only — see [holdPairingSecret]. Writing it down beside the admin token
 * would make one compromised file worth both, and the operator can always set
 * [ENV_PAIRING_SECRET] if they want it to survive a restart.
 */
export const PUT: RequestHandler = async ({ cookies, request }) => {
	if (!unlocked(cookies)) throw error(401, 'unlock the console first');
	if (pairingSecret(env).source === 'env') {
		// Otherwise a browser could quietly replace a secret the operator set in
		// the environment, and the console would then disagree with its own
		// configuration until somebody restarted it.
		throw error(409, `this console takes its pairing secret from ${ENV_PAIRING_SECRET}`);
	}
	const body = await request.json().catch(() => ({}));
	const secret = typeof body?.secret === 'string' ? body.secret.trim() : '';
	if (!secret) throw error(400, 'a pairing secret is required');
	holdPairingSecret(secret);
	return json({ ok: true, source: 'operator' });
};

/** Forget it — the operator handing the machine back, or a typo to correct. */
export const DELETE: RequestHandler = ({ cookies }) => {
	if (!unlocked(cookies)) throw error(401, 'unlock the console first');
	if (pairingSecret(env).source === 'env') {
		throw error(409, `this console takes its pairing secret from ${ENV_PAIRING_SECRET}`);
	}
	dropPairingSecret();
	return json({ ok: true });
};
