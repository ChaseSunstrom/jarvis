import { describe, it, expect, beforeEach, afterEach } from 'vitest';
// Untyped built-ins, named here rather than declared globally — see the same
// note at the top of `consoleAuth.ts`.
// @ts-ignore — @types/node is not a dependency of this project
import * as nodeFs from 'node:fs';
// @ts-ignore — @types/node is not a dependency of this project
import * as nodeOs from 'node:os';
// @ts-ignore — @types/node is not a dependency of this project
import * as nodePath from 'node:path';
import {
	ATTEMPT_WINDOW_MS,
	MAX_ATTEMPTS,
	MAX_SESSIONS,
	MIN_PASSWORD_CHARS,
	clearFailures,
	closeSession,
	dropPairingSecret,
	hashPassword,
	holdPairingSecret,
	isLocked,
	lockedFor,
	looksLikeHash,
	openSession,
	pairingSecret,
	passwordFile,
	recordFailure,
	resetConsoleAuth,
	sessionValid,
	storedPassword,
	verifyPassword,
	writeHash
} from './consoleAuth';

const { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } = nodeFs as {
	mkdtempSync(prefix: string): string;
	readFileSync(path: string, encoding: string): string;
	rmSync(path: string, options: { recursive: boolean; force: boolean }): void;
	statSync(path: string): { mode: number };
	writeFileSync(path: string, data: string, options?: { mode: number }): void;
};
const { tmpdir } = nodeOs as { tmpdir(): string };
const { join } = nodePath as { join(...parts: string[]): string };

let dir = '';
/** An env with the hash file pointed somewhere disposable. */
function env(extra: Record<string, string | undefined> = {}) {
	return { JARVIS_CONSOLE_PASSWORD_FILE: join(dir, 'console-password'), ...extra };
}

beforeEach(() => {
	dir = mkdtempSync(join(tmpdir(), 'jarvis-console-'));
	resetConsoleAuth();
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

describe('the password itself is never stored', () => {
	it('round-trips a password through a salted scrypt hash', async () => {
		const hash = await hashPassword('correct horse battery');
		expect(looksLikeHash(hash)).toBe(true);
		expect(hash).not.toContain('correct horse battery');
		expect(await verifyPassword('correct horse battery', hash)).toBe(true);
		expect(await verifyPassword('correct horse batter', hash)).toBe(false);
		expect(await verifyPassword('', hash)).toBe(false);
	});

	it('salts, so two consoles with the same password do not share a hash', async () => {
		expect(await hashPassword('same password')).not.toBe(await hashPassword('same password'));
	});

	it('ignores whitespace around a pasted password rather than locking the operator out', async () => {
		const hash = await hashPassword('  paste-artefact  ');
		expect(await verifyPassword('paste-artefact', hash)).toBe(true);
	});

	it('refuses an absurd cost from a stored hash instead of spending it', async () => {
		// One edited digit in the hash file, and every login attempt after it
		// asks for a gigabyte (`N`) or a thousand times the work (`p`). Both are
		// refused, and refused QUICKLY: the memory ceiling is a constant, so it
		// still bounds a hash that asks for more. Derive the ceiling from the
		// hash's own parameters instead and this takes seconds each.
		const parts = (await hashPassword('whatever it is')).split('$');
		const hostile = (n: string, p: string) =>
			['scrypt', n, parts[2], p, parts[4], parts[5]].join('$');
		const started = Date.now();
		expect(await verifyPassword('whatever it is', hostile(parts[1], '1000000'))).toBe(false);
		expect(await verifyPassword('whatever it is', hostile(String(1 << 20), parts[3]))).toBe(false);
		expect(Date.now() - started).toBeLessThan(1_000);
	});

	it('refuses a malformed hash rather than throwing', async () => {
		for (const bad of ['', 'hunter2', 'scrypt$16384$8$1$onlyfour', 'bcrypt$1$2$3$a$b']) {
			expect(await verifyPassword('hunter2', bad), bad).toBe(false);
		}
	});
});

describe('where the hash lives', () => {
	it('reports no password when nothing is set anywhere', async () => {
		const stored = await storedPassword(env());
		expect(stored).toEqual({ hash: null, source: 'none', problem: null });
	});

	it('takes a hash from the environment', async () => {
		const hash = await hashPassword('from-the-environment');
		const stored = await storedPassword(env({ JARVIS_CONSOLE_PASSWORD_HASH: hash }));
		expect(stored.source).toBe('env-hash');
		expect(await verifyPassword('from-the-environment', stored.hash!)).toBe(true);
	});

	it('fails closed on a malformed hash in the environment', async () => {
		// The dangerous alternative is falling through to "no password set",
		// which offers the choose-a-password form on a console its operator
		// believes is locked.
		const stored = await storedPassword(env({ JARVIS_CONSOLE_PASSWORD_HASH: 'hunter2' }));
		expect(stored.hash).toBeNull();
		expect(stored.problem).toContain('JARVIS_CONSOLE_PASSWORD_HASH');
	});

	it('hashes a plaintext env password without writing it anywhere', async () => {
		const stored = await storedPassword(env({ JARVIS_CONSOLE_PASSWORD: 'env-plaintext-pw' }));
		expect(stored.source).toBe('env-password');
		expect(await verifyPassword('env-plaintext-pw', stored.hash!)).toBe(true);
		expect(() => statSync(passwordFile(env()))).toThrow();
	});

	it('refuses a too-short env password', async () => {
		const stored = await storedPassword(env({ JARVIS_CONSOLE_PASSWORD: 'short' }));
		expect(stored.hash).toBeNull();
		expect(stored.problem).toContain(String(MIN_PASSWORD_CHARS));
	});

	it('replaces a plaintext password left in the file with its hash', async () => {
		// `echo my-password > .storage/console-password` is what an operator
		// actually does. Accepting it and then not storing it is the point.
		const path = passwordFile(env());
		writeFileSync(path, 'written-by-hand\n');
		const stored = await storedPassword(env());
		expect(stored.source).toBe('file');
		expect(await verifyPassword('written-by-hand', stored.hash!)).toBe(true);
		const onDisk = readFileSync(path, 'utf8');
		expect(onDisk).not.toContain('written-by-hand');
		expect(looksLikeHash(onDisk)).toBe(true);
		expect(statSync(path).mode & 0o077).toBe(0);
	});

	it('writes the hash 0600, even over a world-readable file', async () => {
		const path = passwordFile(env());
		writeFileSync(path, 'placeholder\n', { mode: 0o644 });
		writeHash(env(), await hashPassword('a-chosen-password'));
		expect(statSync(path).mode & 0o077).toBe(0);
		const stored = await storedPassword(env());
		expect(await verifyPassword('a-chosen-password', stored.hash!)).toBe(true);
	});
});

describe('sessions', () => {
	it('accepts the token it issued and nothing else', () => {
		const token = openSession();
		expect(sessionValid(token)).toBe(true);
		expect(sessionValid(`${token}x`)).toBe(false);
		expect(sessionValid('')).toBe(false);
		expect(sessionValid(undefined)).toBe(false);
	});

	it('expires, so a tab left open overnight is not still unlocked', () => {
		const token = openSession(1_000);
		expect(sessionValid(token, 1_000)).toBe(true);
		expect(sessionValid(token, 1_000 + 13 * 60 * 60 * 1000)).toBe(false);
	});

	it('forgets one on request, which is what LOCK means', () => {
		const token = openSession();
		closeSession(token);
		expect(sessionValid(token)).toBe(false);
	});

	it('bounds how many it keeps', () => {
		const first = openSession();
		for (let i = 0; i < MAX_SESSIONS + 2; i += 1) openSession();
		expect(sessionValid(first)).toBe(false);
	});
});

describe('attempt limiting', () => {
	// An unlimited endpoint is not a password: scrypt costs a guess ~30ms, so
	// unbounded means tens of guesses a second and silence while it happens.
	it('shuts the door on a caller after MAX_ATTEMPTS inside the window', () => {
		for (let i = 0; i < MAX_ATTEMPTS - 1; i += 1) recordFailure('1.2.3.4', 1_000);
		expect(isLocked('1.2.3.4', 1_000)).toBe(false);
		recordFailure('1.2.3.4', 1_000);
		expect(isLocked('1.2.3.4', 1_000)).toBe(true);
		expect(lockedFor('1.2.3.4', 1_000)).toBeGreaterThan(0);
	});

	it('locks out only the caller that failed', () => {
		// A global counter is a denial of service anybody can trigger: five
		// wrong guesses and the operator cannot reach their own console.
		for (let i = 0; i < MAX_ATTEMPTS; i += 1) recordFailure('attacker', 1_000);
		expect(isLocked('attacker', 1_000)).toBe(true);
		expect(isLocked('the-operator', 1_000)).toBe(false);
	});

	it('forgives once the window has passed', () => {
		for (let i = 0; i < MAX_ATTEMPTS; i += 1) recordFailure('1.2.3.4', 1_000);
		expect(isLocked('1.2.3.4', 1_000 + ATTEMPT_WINDOW_MS + 1)).toBe(false);
	});

	it('forgets a caller that got it right', () => {
		for (let i = 0; i < MAX_ATTEMPTS; i += 1) recordFailure('1.2.3.4', 1_000);
		clearFailures('1.2.3.4');
		expect(isLocked('1.2.3.4', 1_000)).toBe(false);
	});
});

describe('the pairing secret this console holds', () => {
	it('prefers the environment, and a browser may not replace that', () => {
		holdPairingSecret('typed-into-the-panel');
		expect(pairingSecret({ JARVIS_PAIRING_SECRET: 'from-the-env' })).toEqual({
			secret: 'from-the-env',
			source: 'env'
		});
	});

	it('holds one an operator handed over, in memory only', () => {
		expect(pairingSecret({}).source).toBe('none');
		holdPairingSecret('  typed-into-the-panel  ');
		expect(pairingSecret({})).toEqual({ secret: 'typed-into-the-panel', source: 'operator' });
		dropPairingSecret();
		expect(pairingSecret({}).source).toBe('none');
	});
});
