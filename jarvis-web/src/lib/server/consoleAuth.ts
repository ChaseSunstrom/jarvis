/**
 * A password on the console, and the pairing secret it unlocks.
 *
 * There was no login here. That was survivable only because the console was
 * assumed to be behind something else — but it is not a private page: the `/ws`
 * relay attaches the server-held admin token to whatever connects, and the
 * origin guard deliberately admits a client that sends no `Origin`, because
 * that is what a non-browser client looks like. So anything that can reach this
 * port is already an authenticated API client. The pairing secret was the one
 * thing standing between that and a PERMANENT credential, and it stood there by
 * being typed in full by a person, every session.
 *
 * This replaces the typing, not the second factor. The operator proves a
 * password once per browser session; the console holds the pairing secret and
 * uses it on their behalf. What is deliberately preserved: reaching the port is
 * still not enough. What genuinely changes: an attacker who can read this
 * server's environment or filesystem now gets the pairing secret too — but that
 * attacker already has `JARVIS_TOKEN` sitting in the same environment, so the
 * pairing secret was never what was protecting them from.
 *
 * Rules that follow from that:
 *
 * **The password is never stored.** A scrypt hash is (`node:crypto`), salted,
 * and a file that arrives holding a plaintext password is rewritten with its
 * hash the first time it is read.
 *
 * **The password never reaches the browser**, and neither does the pairing
 * secret until a session has been proved server-side. A control that fetched
 * the secret and then hid it behind a button would have already lost.
 *
 * **Attempts are counted, per caller.** See [isLocked].
 */

/*
 * `@types/node` is not a dependency of this project, so the built-ins arrive
 * untyped and `svelte-check` refuses the import outright (`wsProxy.test.ts`
 * carries the same gap). Suppressing the import and then naming the shape used
 * keeps the checking where it matters — a mistyped scrypt argument is a
 * silently weaker hash — without a project-wide ambient declaration that would
 * collide the day somebody does install the types.
 */
// @ts-ignore — see above
import * as nodeCrypto from 'node:crypto';
// @ts-ignore — see above
import * as nodeFs from 'node:fs';
// @ts-ignore — see above
import * as nodePath from 'node:path';

/** A node Buffer, as far as this module is concerned. */
type Bytes = Uint8Array & { toString(encoding: string): string };

const { randomBytes, randomUUID, scrypt, timingSafeEqual } = nodeCrypto as {
	randomBytes(size: number): Bytes;
	randomUUID(): string;
	scrypt(
		password: string,
		salt: Uint8Array,
		keylen: number,
		options: { N: number; r: number; p: number; maxmem: number },
		callback: (err: Error | null, key: Bytes) => void
	): void;
	timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean;
};

const { chmodSync, mkdirSync, readFileSync, writeFileSync } = nodeFs as {
	chmodSync(path: string, mode: number): void;
	mkdirSync(path: string, options: { recursive: boolean }): void;
	readFileSync(path: string, encoding: string): string;
	writeFileSync(path: string, data: string, options: { mode: number }): void;
};

const { dirname, isAbsolute, join } = nodePath as {
	dirname(path: string): string;
	isAbsolute(path: string): boolean;
	join(...parts: string[]): string;
};

declare const Buffer: { from(input: string, encoding?: string): Bytes };
declare const process: { cwd(): string };

/** Plaintext password, hashed in memory at first use and never written out. */
export const ENV_PASSWORD = 'JARVIS_CONSOLE_PASSWORD';
/** A `scrypt$…` line produced by [hashPassword]. Nothing recoverable anywhere. */
export const ENV_PASSWORD_HASH = 'JARVIS_CONSOLE_PASSWORD_HASH';
/** Where the hash file lives, when the default is not wanted. */
export const ENV_PASSWORD_FILE = 'JARVIS_CONSOLE_PASSWORD_FILE';
/** The pairing secret this console mints with, set where the console runs. */
export const ENV_PAIRING_SECRET = 'JARVIS_PAIRING_SECRET';

/**
 * Beside jarvis-core's own `.storage/auth.json`, which holds token hashes for
 * the same reason and is already ignored by git everywhere in this repo.
 */
export const DEFAULT_PASSWORD_FILE = '.storage/console-password';

/**
 * Shorter than this is a placeholder, not a password. Higher than
 * `api/pairing.py`'s MIN_SECRET_CHARS because that guards a generated secret
 * and this guards one a person invents at a keyboard.
 */
export const MIN_PASSWORD_CHARS = 10;

/**
 * scrypt cost. 16384/8/1 is the interactive-login setting: ~16 MiB and tens of
 * milliseconds per attempt, which is what makes a stolen hash expensive to
 * chew through offline. [MAX_ATTEMPTS] is what bounds the online guess rate —
 * these two are not substitutes for each other.
 */
export const SCRYPT = { N: 16384, r: 8, p: 1, keylen: 32 } as const;

/**
 * The most memory any stored hash may ask this process to allocate — four
 * times what [SCRYPT] itself needs, and deliberately a CONSTANT rather than
 * something derived from the parameters in the hash. Those parameters come out
 * of an editable file, and scrypt's `N`, `r` and `p` are each an instruction to
 * spend memory: a ceiling computed from them is not a ceiling. With one, every
 * malformed or hostile cost is refused by the library before it allocates
 * anything, which is why nothing below re-validates them by hand.
 */
const MAX_MEM = 128 * SCRYPT.N * SCRYPT.r * 4;

function derive(
	password: string,
	salt: Bytes,
	params: { N: number; r: number; p: number; keylen: number }
): Promise<Bytes> {
	return new Promise((resolve, reject) => {
		scrypt(
			// NFKC so a password typed on a phone keyboard and the same password
			// typed on a laptop are the same bytes; two Unicode spellings of one
			// string locking the operator out is not a hypothetical.
			password.normalize('NFKC'),
			salt,
			params.keylen,
			{ N: params.N, r: params.r, p: params.p, maxmem: MAX_MEM },
			(err, key) => (err ? reject(err) : resolve(key))
		);
	});
}

/** `scrypt$N$r$p$salt$key`, all base64. One line, so a file can hold it. */
export async function hashPassword(password: string): Promise<string> {
	const salt = randomBytes(16);
	const key = await derive(password.trim(), salt, SCRYPT);
	return `scrypt$${SCRYPT.N}$${SCRYPT.r}$${SCRYPT.p}$${salt.toString('base64')}$${key.toString('base64')}`;
}

export function looksLikeHash(line: string): boolean {
	return /^scrypt\$\d+\$\d+\$\d+\$[A-Za-z0-9+/]+={0,2}\$[A-Za-z0-9+/]+={0,2}$/.test(line.trim());
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
	const parts = String(stored ?? '').trim().split('$');
	if (parts.length !== 6 || parts[0] !== 'scrypt') return false;
	const [, n, r, p, saltB64, keyB64] = parts;
	const N = Number(n);
	const rr = Number(r);
	const pp = Number(p);
	const salt = Buffer.from(saltB64, 'base64');
	const expected = Buffer.from(keyB64, 'base64');
	if (!salt.length || expected.length < 16) return false;
	let key: Bytes;
	try {
		key = await derive(String(password ?? '').trim(), salt, {
			N,
			r: rr,
			p: pp,
			keylen: expected.length
		});
	} catch {
		return false;
	}
	return key.length === expected.length && timingSafeEqual(key, expected);
}

// --- where the hash lives ---------------------------------------------------

export type HashSource = 'env-hash' | 'env-password' | 'file' | 'none';

export interface StoredPassword {
	/** The hash in force, or null when no password has been chosen yet. */
	hash: string | null;
	source: HashSource;
	/** Something the operator has to fix, e.g. a malformed hash in the env. */
	problem: string | null;
}

export function passwordFile(env: Record<string, string | undefined>): string {
	const configured = (env[ENV_PASSWORD_FILE] ?? '').trim();
	if (configured) return isAbsolute(configured) ? configured : join(process.cwd(), configured);
	return join(process.cwd(), DEFAULT_PASSWORD_FILE);
}

/** Hashing the env password on every request would cost 30ms per request. */
let envMemo: { password: string; hash: string } | null = null;

export async function storedPassword(
	env: Record<string, string | undefined>
): Promise<StoredPassword> {
	const envHash = (env[ENV_PASSWORD_HASH] ?? '').trim();
	if (envHash) {
		if (looksLikeHash(envHash)) return { hash: envHash, source: 'env-hash', problem: null };
		// Fail closed and say so. Silently falling through to "no password set"
		// would offer the next visitor the choose-a-password form on a console
		// its operator believes is locked.
		return {
			hash: null,
			source: 'none',
			problem: `${ENV_PASSWORD_HASH} is not a scrypt hash — unset it, or set ${ENV_PASSWORD} instead`
		};
	}

	const envPassword = (env[ENV_PASSWORD] ?? '').trim();
	if (envPassword) {
		if (envPassword.length < MIN_PASSWORD_CHARS) {
			return {
				hash: null,
				source: 'none',
				problem: `${ENV_PASSWORD} is shorter than ${MIN_PASSWORD_CHARS} characters`
			};
		}
		if (envMemo?.password !== envPassword) {
			envMemo = { password: envPassword, hash: await hashPassword(envPassword) };
		}
		return { hash: envMemo.hash, source: 'env-password', problem: null };
	}

	let contents = '';
	try {
		contents = readFileSync(passwordFile(env), 'utf8').trim();
	} catch {
		return { hash: null, source: 'none', problem: null };
	}
	if (!contents) return { hash: null, source: 'none', problem: null };
	if (looksLikeHash(contents)) return { hash: contents, source: 'file', problem: null };

	// `echo hunter2hunter2 > .storage/console-password` is what an operator will
	// actually do, and refusing it would push them towards the plaintext env var
	// instead. So it is accepted once and immediately replaced by its hash: the
	// password is not stored, it is only briefly on its way to not being stored.
	if (contents.length < MIN_PASSWORD_CHARS) {
		return {
			hash: null,
			source: 'none',
			problem: `the password in ${passwordFile(env)} is shorter than ${MIN_PASSWORD_CHARS} characters`
		};
	}
	const hash = await hashPassword(contents);
	writeHash(env, hash);
	return { hash, source: 'file', problem: null };
}

/** Write the hash, 0600, creating the directory. Throws on failure — a password
 *  that reports success and is not persisted is worse than a visible error. */
export function writeHash(env: Record<string, string | undefined>, hash: string): void {
	const path = passwordFile(env);
	mkdirSync(dirname(path), { recursive: true });
	writeFileSync(path, `${hash}\n`, { mode: 0o600 });
	// `mode` on writeFileSync only applies when the file is created, so an
	// existing world-readable file would keep its mode through a rotation.
	chmodSync(path, 0o600);
}

// --- sessions ---------------------------------------------------------------

export const SESSION_COOKIE = 'jarvis_console';
/** Long enough to set a house up in one sitting; short enough to expire. */
export const SESSION_TTL_MS = 12 * 60 * 60 * 1000;
/** One console, a handful of tabs. A cap keeps the map from being a memory leak. */
export const MAX_SESSIONS = 64;

const sessions = new Map<string, number>();

export function openSession(now = Date.now()): string {
	for (const [token, expires] of sessions) if (expires <= now) sessions.delete(token);
	while (sessions.size >= MAX_SESSIONS) {
		// Oldest expiry first: the session closest to being useless anyway.
		const oldest = [...sessions].sort((a, b) => a[1] - b[1])[0];
		sessions.delete(oldest[0]);
	}
	const token = `${randomUUID()}.${randomBytes(16).toString('base64url')}`;
	sessions.set(token, now + SESSION_TTL_MS);
	return token;
}

export function sessionValid(token: string | undefined | null, now = Date.now()): boolean {
	const offered = String(token ?? '');
	if (!offered) return false;
	const candidate = Buffer.from(offered);
	// Constant-time against every live session, and the loop does not stop
	// early on a match — the same rule `PairingCodes.claim` follows, for the
	// same reason: this is the check that stands in front of the credential.
	let ok = false;
	for (const [issued, expires] of sessions) {
		const known = Buffer.from(issued);
		const same = known.length === candidate.length && timingSafeEqual(known, candidate);
		if (same && expires > now) ok = true;
		if (same && expires <= now) sessions.delete(issued);
	}
	return ok;
}

export function closeSession(token: string | undefined | null): void {
	if (token) sessions.delete(String(token));
}

// --- attempt limiting -------------------------------------------------------

/**
 * An unlimited password endpoint is not a password. scrypt costs an attacker
 * ~30ms per guess, so an unbounded endpoint hands them roughly thirty guesses
 * a second per connection and as many connections as they like: every password
 * a person would actually choose falls inside an afternoon, and the console
 * never says a word while it happens. These bound the online rate; the scrypt
 * cost only bounds the offline one.
 *
 * Counted per caller, not globally, for the reason `api/pairing.py` spells out:
 * a global counter is a denial of service anybody can trigger, locking the
 * operator out of their own console by failing five logins.
 */
export const MAX_ATTEMPTS = 5;
export const ATTEMPT_WINDOW_MS = 300_000;
/** A spoofable key must not be able to grow this map without bound. */
export const MAX_TRACKED_CLIENTS = 256;

const failures = new Map<string, number[]>();

function recent(client: string, now: number): number[] {
	const kept = (failures.get(client) ?? []).filter((at) => now - at < ATTEMPT_WINDOW_MS);
	if (kept.length) failures.set(client, kept);
	else failures.delete(client);
	return kept;
}

export function isLocked(client: string, now = Date.now()): boolean {
	return recent(client, now).length >= MAX_ATTEMPTS;
}

export function recordFailure(client: string, now = Date.now()): void {
	const kept = recent(client, now);
	if (failures.size >= MAX_TRACKED_CLIENTS && !failures.has(client)) {
		// Drop the least recently failing caller. That forgives somebody who
		// was already being refused, which is a better failure than an
		// unbounded map keyed by a value the caller can choose.
		const oldest = [...failures].sort((a, b) => a[1][a[1].length - 1] - b[1][b[1].length - 1])[0];
		failures.delete(oldest[0]);
	}
	failures.set(client, [...kept, now]);
}

export function clearFailures(client: string): void {
	failures.delete(client);
}

/** Seconds until [client] may try again, or 0. */
export function lockedFor(client: string, now = Date.now()): number {
	const kept = recent(client, now);
	if (kept.length < MAX_ATTEMPTS) return 0;
	return Math.max(1, Math.ceil((ATTEMPT_WINDOW_MS - (now - kept[0])) / 1000));
}

// --- the pairing secret this console holds ----------------------------------

export type SecretSource = 'env' | 'operator' | 'none';

/**
 * Handed over by an operator who proved the password, and kept in this
 * process's memory only — never written to disk, never sent to the browser
 * except through the password-gated reveal. A restart forgets it, which is the
 * cost of not writing down the thing that guards permanent credentials.
 */
let heldSecret = '';

export function pairingSecret(env: Record<string, string | undefined>): {
	secret: string;
	source: SecretSource;
} {
	const configured = (env[ENV_PAIRING_SECRET] ?? '').trim();
	if (configured) return { secret: configured, source: 'env' };
	if (heldSecret) return { secret: heldSecret, source: 'operator' };
	return { secret: '', source: 'none' };
}

export function holdPairingSecret(value: string): void {
	heldSecret = String(value ?? '').trim();
}

export function dropPairingSecret(): void {
	heldSecret = '';
}

/** Test seam. Module state is deliberate — these are per-process, not per-request. */
export function resetConsoleAuth(): void {
	sessions.clear();
	failures.clear();
	heldSecret = '';
	envMemo = null;
}
