import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

/**
 * The console's central promise, checked against the source rather than
 * against the routes somebody remembered to test.
 *
 * jarvis-web holds an admin token that can read every state and dispatch any
 * service, `lock.unlock` included. The browser must never receive it — that is
 * the whole reason the relay and these server routes exist rather than the page
 * talking to jarvis-core directly.
 *
 * Every route that attaches it is therefore a security boundary, and each one
 * has its own guard: `/api/tts` an allow-list of media paths, `/api/pair` the
 * operator's second secret. The danger is not those; it is the SIXTH route,
 * written in six months, that reaches for `backend.token` because the ones
 * around it do and inherits none of their guards.
 *
 * So this walks the route tree. A new route that attaches the token has to be
 * named here — which is a thirty-second edit and a moment's thought about what
 * guards it, and that moment is the entire point.
 */

const ROUTES = new URL('../../routes', import.meta.url).pathname;

/**
 * Routes allowed to attach the server-held admin token, and what stops each
 * from being a way to reach the backend with it.
 */
const MAY_ATTACH_THE_TOKEN: Record<string, string> = {
	'api/config/+server.ts':
		'reports only Boolean(token) as `tokenConfigured`; the value never leaves',
	'api/tts/+server.ts':
		'allow-listed to media paths by mediaProxyTarget, which re-tests the NORMALISED url',
	'api/pair/+server.ts':
		'needs JARVIS_PAIRING_SECRET, typed by the operator and never stored here'
};

function serverRoutes(dir: string, prefix = ''): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		const full = join(dir, entry);
		if (statSync(full).isDirectory()) {
			found.push(...serverRoutes(full, `${prefix}${entry}/`));
		} else if (entry === '+server.ts') {
			found.push(`${prefix}${entry}`);
		}
	}
	return found;
}

describe('the admin token never reaches the browser', () => {
	const routes = serverRoutes(ROUTES);

	it('finds the routes at all, so a passing run means something', () => {
		// Without this the whole file passes vacuously if the tree moves.
		expect(routes.length).toBeGreaterThanOrEqual(4);
		expect(routes).toContain('api/tts/+server.ts');
	});

	it('only the named routes touch the backend token', () => {
		const touching = routes.filter((route) =>
			readFileSync(join(ROUTES, route), 'utf8').includes('backend.token')
		);
		const unexpected = touching.filter((route) => !(route in MAY_ATTACH_THE_TOKEN));
		expect(
			unexpected,
			'a new server route attaches the admin token. That is allowed — but say ' +
				'here what stops it being a way to reach the backend with it, the way ' +
				'/api/tts is allow-listed and /api/pair needs a second secret.'
		).toEqual([]);
	});

	it('every named route still exists', () => {
		// A stale entry is worse than a missing one: it reads as a considered
		// exemption for a guard nobody has looked at since the file was deleted.
		for (const route of Object.keys(MAY_ATTACH_THE_TOKEN)) {
			expect(routes, `${route} is exempted and no longer exists`).toContain(route);
		}
	});

	it('no route ever sends the token to the page', () => {
		for (const route of routes) {
			const source = readFileSync(join(ROUTES, route), 'utf8');
			// The token may be put in an outbound `Authorization` header. It may
			// not be put in a response body — and `json({...backend})` is how
			// that happens by accident, spreading a config object that has it.
			expect(source, `${route} spreads the backend config into a response`).not.toMatch(
				/(json|Response)\s*\(\s*\{?\s*\.\.\.\s*backend/
			);
			expect(source, `${route} puts the token in a response body`).not.toMatch(
				/body:\s*JSON\.stringify\([^)]*backend\.token/
			);
		}
	});

	it('every route that proxies upstream refuses to follow a redirect', () => {
		// A 30x can move a request — and whatever credential it carries — to a
		// host the operator never configured. Off at every call site rather
		// than trusted to a default.
		for (const route of routes) {
			const source = readFileSync(join(ROUTES, route), 'utf8');
			if (!source.includes('fetch(')) continue;
			expect(source, `${route} proxies upstream without redirect: 'error'`).toContain(
				"redirect: 'error'"
			);
		}
	});
});
