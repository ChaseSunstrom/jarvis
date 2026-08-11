import { error, json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import {
	ENV_PASSWORD,
	MIN_PASSWORD_CHARS,
	SESSION_COOKIE,
	clearFailures,
	closeSession,
	hashPassword,
	isLocked,
	lockedFor,
	openSession,
	passwordFile,
	recordFailure,
	sessionValid,
	storedPassword,
	verifyPassword,
	writeHash
} from '$lib/server/consoleAuth';
import type { RequestHandler } from './$types';

// The console's login.
//
// It exists because reaching this server is already enough to use its admin
// token — the `/ws` relay attaches it to whatever connects. See
// `$lib/server/consoleAuth` for what that does and does not change, and
// `api/pair/+server.ts` for what the password now unlocks.

/** Rate-limit bucket. Only ever a key, so a spoofed one wins a fresh allowance
 *  and nothing else — exactly the trade `api/pairing.py` documents. */
function clientKey(getClientAddress: () => string): string {
	try {
		return getClientAddress() || 'unknown';
	} catch {
		return 'unknown';
	}
}

/** Where the operator stands: is there a password, and are they past it? */
export const GET: RequestHandler = async ({ cookies }) => {
	const stored = await storedPassword(env);
	return json({
		configured: Boolean(stored.hash),
		authenticated: sessionValid(cookies.get(SESSION_COOKIE)),
		source: stored.source,
		problem: stored.problem,
		minChars: MIN_PASSWORD_CHARS,
		// One line of documentation, rendered in the panel: the two places an
		// operator can put it instead of choosing one in the browser.
		envVar: ENV_PASSWORD,
		file: passwordFile(env)
	});
};

/**
 * Unlock — choosing the password if none has been chosen yet.
 *
 * First load on a console with no password is the one moment the choice can be
 * made without one, which is why it is bounded by "no password exists" and
 * nothing else: the alternative is a console that cannot be locked at all until
 * somebody edits a file on the server, and an unlockable console is what this
 * whole file exists to end.
 */
export const POST: RequestHandler = async ({ cookies, request, url, getClientAddress }) => {
	const who = clientKey(getClientAddress);
	if (isLocked(who)) {
		throw error(429, `too many attempts — wait ${lockedFor(who)}s and try again`);
	}

	const body = await request.json().catch(() => ({}));
	const password = typeof body?.password === 'string' ? body.password : '';
	const stored = await storedPassword(env);
	if (stored.problem) throw error(500, stored.problem);

	let chosen = false;
	if (!stored.hash) {
		if (password.trim().length < MIN_PASSWORD_CHARS) {
			throw error(400, `choose a password of at least ${MIN_PASSWORD_CHARS} characters`);
		}
		// Hash first, write second: a failed write must not leave the console
		// reporting a password that nothing on disk agrees with.
		const hash = await hashPassword(password);
		try {
			writeHash(env, hash);
		} catch {
			throw error(
				500,
				`could not write ${passwordFile(env)} — set ${ENV_PASSWORD} where the console runs instead`
			);
		}
		chosen = true;
	} else if (!(await verifyPassword(password, stored.hash))) {
		// Counted before the refusal is sent, so a client that hangs up early
		// still spent its attempt.
		recordFailure(who);
		throw error(401, 'that console password is not correct');
	}

	clearFailures(who);
	cookies.set(SESSION_COOKIE, openSession(), {
		path: '/',
		httpOnly: true, // page JS never sees it, so an XSS cannot lift the session
		sameSite: 'strict', // and a hostile page's POST arrives without it
		secure: url.protocol === 'https:'
		// No maxAge: a browser-session cookie, so "once per session" is the
		// browser's own definition of a session rather than a number here.
	});
	return json({ ok: true, chosen });
};

/** Lock it again — the operator leaving the machine, not an expiry. */
export const DELETE: RequestHandler = ({ cookies, url }) => {
	closeSession(cookies.get(SESSION_COOKIE));
	cookies.delete(SESSION_COOKIE, { path: '/', secure: url.protocol === 'https:' });
	return json({ ok: true });
};
