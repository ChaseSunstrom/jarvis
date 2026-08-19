/**
 * The diff reader, and the two sentences on the Code page.
 *
 * The header case below is the one that matters: `---` and `+++` start with
 * the same characters as a removed and an added line, so a naive reader paints
 * every file header as a deletion and an added file looks like a removed one.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
	MAX_DIFF_LINES,
	describeEnvironment,
	suggestedName,
	whyNoChecks,
	whyNotName,
	whyNotProject,
	type CodeForge,
	type CodeEnvironment,
	countChanges,
	describeChecks,
	describeRepo,
	describeSandbox,
	parseDiff,
	whyNotStart,
	type CodeRepo
} from './code';

const DIFF = `diff --git a/src/app.py b/src/app.py
index 1234567..89abcde 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def handle():
-    return 1
+    return 2
`;

function repo(over: Partial<CodeRepo> = {}): CodeRepo {
	return {
		name: 'project',
		path: '/src/project',
		description: '',
		checks: [],
		writable: true,
		...over
	};
}

describe('parseDiff', () => {
	it('does not mistake a file header for a changed line', () => {
		const lines = parseDiff(DIFF);
		const headers = lines.filter((l) => l.text.startsWith('---') || l.text.startsWith('+++'));
		expect(headers).toHaveLength(2);
		expect(headers.every((l) => l.kind === 'meta')).toBe(true);
	});

	it('finds the one added and the one removed line', () => {
		expect(countChanges(parseDiff(DIFF))).toEqual({ added: 1, removed: 1 });
	});

	it('marks the hunk header so it can be a separator', () => {
		expect(parseDiff(DIFF).filter((l) => l.kind === 'hunk')).toHaveLength(1);
	});

	it('keeps context lines as context', () => {
		expect(parseDiff(DIFF).some((l) => l.kind === 'context' && l.text.includes('def handle'))).toBe(
			true
		);
	});

	it('is empty for no diff rather than one blank row', () => {
		expect(parseDiff('')).toEqual([]);
	});

	it('truncates rather than rendering a hundred thousand rows', () => {
		const huge = Array.from({ length: MAX_DIFF_LINES + 500 }, (_, i) => `+line ${i}`).join('\n');
		const lines = parseDiff(huge);
		expect(lines.length).toBe(MAX_DIFF_LINES + 1);
		expect(lines.at(-1)?.text).toContain('truncated');
	});

	it('treats a new-file header as metadata, not as an addition', () => {
		const lines = parseDiff('new file mode 100644\n+++ b/x\n+hello\n');
		expect(countChanges(lines).added).toBe(1);
	});
});

describe('whyNotStart', () => {
	it('needs a repository', () => {
		expect(whyNotStart(null, 'change the handler')).toContain('repository');
	});

	it('needs an instruction', () => {
		expect(whyNotStart(repo(), '   ')).toContain('what to change');
	});

	it('refuses an instruction too short to act on', () => {
		// The job cannot ask a follow-up question, so "fix it" is three minutes
		// of guessing followed by a diff nobody wanted.
		expect(whyNotStart(repo(), 'fix it')).toContain('bit more');
	});

	it('allows a real instruction', () => {
		expect(whyNotStart(repo(), 'make handle() return 2')).toBe('');
	});

	it('allows one against a read-only repository', () => {
		// It will produce a report rather than a branch, and `describeRepo`
		// says so — but refusing it would remove the useful half of read-only.
		expect(whyNotStart(repo({ writable: false }), 'explain how routing works')).toBe('');
	});
});

describe('describeRepo', () => {
	it('leads with whether it can be changed', () => {
		expect(describeRepo(repo({ writable: false }))).toContain('read-only');
		expect(describeRepo(repo({ writable: true }))).toContain('may be changed');
	});

	it('names the checks, or says there are none', () => {
		// Read-only, because a WRITABLE repository with no environment does not
		// get to run its checks at all — see `whyNoChecks`.
		expect(
			describeRepo(repo({ checks: ['pytest -q'], writable: false }))
		).toContain('pytest -q');
		expect(describeRepo(repo())).toContain('no checks');
	});

	it('does not list checks that will never run', () => {
		const said = describeRepo(repo({ checks: ['pytest -q'], writable: true }));
		expect(said).toContain('withheld');
		expect(said).not.toContain('pytest -q');
	});
});

describe('whyNoChecks', () => {
	/**
	 * A check is the operator's command string, but it EXECUTES files out of
	 * the working tree, and on a writable repository a job can write those. So
	 * jarvis-core withholds `run_check` unless something stands between the
	 * check and the machine.
	 */
	it('explains the one configuration where checks are withheld', () => {
		const said = whyNoChecks(repo({ checks: ['pytest -q'], writable: true }));
		expect(said).toContain('withheld');
		expect(said).toMatch(/environment/i);
		expect(said).toMatch(/read-only/i);
	});

	it('is quiet when there is an environment', () => {
		expect(
			whyNoChecks(repo({ checks: ['pytest -q'], writable: true, environment: 'python' }))
		).toBe('');
	});

	it('is quiet when the operator set a sandbox wrapper', () => {
		expect(whyNoChecks(repo({ checks: ['pytest -q'], writable: true }), true)).toBe('');
	});

	it('is quiet on a read-only repository, where the files are the operator’s', () => {
		expect(whyNoChecks(repo({ checks: ['pytest -q'], writable: false }))).toBe('');
	});

	it('is quiet when there are no checks to withhold', () => {
		expect(whyNoChecks(repo({ writable: true }))).toBe('');
	});
});

describe('describeChecks', () => {
	it('says nothing when a job ran none', () => {
		expect(describeChecks([])).toBe('');
	});

	it('counts the passing ones', () => {
		expect(
			describeChecks([
				{ command: 'a', ok: true, output: '' },
				{ command: 'b', ok: false, output: '' }
			])
		).toBe('1/2 checks passed');
	});

	it('is singular for one', () => {
		expect(describeChecks([{ command: 'a', ok: true, output: '' }])).toBe('1/1 check passed');
	});
});

describe('describeSandbox', () => {
	it('does not call an unwrapped server unsandboxed', () => {
		// The model still has no shell and cannot leave the repository. The
		// only thing the wrapper changes is where the CHECKS run, and the
		// sentence has to say that rather than imply the rest is off.
		const off = describeSandbox(false);
		expect(off).toMatch(/environment/i);
		expect(off).not.toMatch(/not sandboxed|unsafe/i);
	});

	it('says so when there is one', () => {
		expect(describeSandbox(true)).toContain('wrapper');
	});
});


function environment(over: Partial<CodeEnvironment> = {}): CodeEnvironment {
	return {
		name: 'python',
		image: 'python:3.12',
		network: 'none',
		memory: '2g',
		cpus: '2',
		env: [],
		setup: [],
		...over
	};
}

describe('whyNotName', () => {
	it('allows an ordinary name', () => {
		for (const ok of ['snake', 'snake-opengl', 'a1', 'my.project', 'a_b']) {
			expect(whyNotName(ok)).toBe('');
		}
	});

	it('refuses a name that is a path in disguise', () => {
		// The name becomes a directory. jarvis-core refuses these too — this
		// copy exists so the form can say why before a round trip, not so the
		// browser can decide.
		for (const bad of ['../etc', 'a/b', '..', 'a b']) {
			expect(whyNotName(bad)).not.toBe('');
		}
	});

	it('insists on lowercase, and says why', () => {
		expect(whyNotName('Snake')).toContain('lowercase');
	});

	it('refuses a reserved name', () => {
		expect(whyNotName('node_modules')).toContain('reserved');
		expect(whyNotName('git')).toContain('reserved');
	});

	it('refuses an empty name and one that is too long', () => {
		expect(whyNotName('   ')).toContain('needs a name');
		expect(whyNotName('x'.repeat(65))).toContain('too long');
	});
});

describe('describeEnvironment', () => {
	it('says plainly that no environment means no shell', () => {
		const said = describeEnvironment(null);
		expect(said).toContain('No shell');
		// Deliberately silent about checks: whether they run depends on the
		// repository, not the environment, and `whyNoChecks` is what knows.
		expect(said).not.toContain('checks');
	});

	it('leads with what the network can do, because that is the choice', () => {
		expect(describeEnvironment(environment({ network: 'egress' }))).toContain(
			'reach the internet'
		);
		expect(describeEnvironment(environment())).toContain('no network');
	});

	it('names the image, so the reader knows what is in it', () => {
		expect(describeEnvironment(environment({ image: 'gcc:14' }))).toContain('gcc:14');
	});
});

describe('whyNotProject', () => {
	/**
	 * The console's allow-list check is a copy of jarvis-core's `permits()`,
	 * so the form can refuse before a round trip. The copy is for the message
	 * and never for the decision — but a copy that DRIFTS is worse than none,
	 * because the form accepts what the server rejects and the reader blames
	 * the form.
	 *
	 * So this table is not written here. `tests/contracts/forge_allow_list.json`
	 * is read by BOTH suites: jarvis-core asserts `permits()` against it in
	 * `test_the_console_and_the_server_agree_about_the_allow_list`, and this
	 * asserts the console against the same rows. Neither side owns the
	 * answers, and a row added to one is a row the other has to satisfy.
	 */
	const table = JSON.parse(
		readFileSync(
			fileURLToPath(new URL('../../../tests/contracts/forge_allow_list.json', import.meta.url)),
			'utf-8'
		)
	) as { cases: { allow: string[]; project: string; permitted: boolean }[] };

	const forgeWith = (allow: string[], extra: Partial<CodeForge> = {}): CodeForge => ({
		name: 'work',
		kind: 'github',
		host: 'github.com',
		has_token: true,
		allow,
		push: true,
		...extra
	});

	it('agrees with jarvis-core on every row of the shared table', () => {
		expect(table.cases.length).toBeGreaterThanOrEqual(15);
		// Every row against every combination of the two flags that have
		// nothing to do with the decision. An early return for "no token" once
		// answered "permitted" before the allow-list ran, and a table checked
		// under one flag combination would never have seen it.
		for (const has_token of [true, false]) {
			for (const push of [true, false]) {
				for (const row of table.cases) {
					const forge = forgeWith(row.allow, { has_token, push });
					const problem = whyNotProject(forge, row.project);
					expect(
						problem === '',
						`allow=${JSON.stringify(row.allow)} project=${JSON.stringify(row.project)} ` +
							`has_token=${has_token} push=${push}: ` +
							`console said ${problem || '<permitted>'}, table says ${row.permitted}`
					).toBe(row.permitted);
				}
			}
		}
	});

	it('asks for a forge before it asks for anything else', () => {
		expect(whyNotProject(null, 'owner/repo')).toContain('forge');
	});

	it('says which repositories are permitted, not just that this one is not', () => {
		const problem = whyNotProject(forgeWith(['acme/widgets']), 'other/thing');
		expect(problem).toContain('acme/widgets');
	});

	it('does not refuse a public clone from a forge with no token', () => {
		const forge = { ...forgeWith(['acme/widgets']), has_token: false };
		expect(whyNotProject(forge, 'acme/widgets')).toBe('');
	});
});

describe('suggestedName', () => {
	it('is the last segment, the way git clone would name it', () => {
		expect(suggestedName('acme/widgets')).toBe('widgets');
		expect(suggestedName('group/sub/Thing')).toBe('thing');
	});

	it('drops a trailing .git, which is not part of the name', () => {
		expect(suggestedName('acme/widgets.git')).toBe('widgets');
	});

	it('produces something whyNotName accepts, or nothing at all', () => {
		for (const project of ['acme/widgets', 'A/B', 'x/y.git', '']) {
			const name = suggestedName(project);
			if (name) expect(whyNotName(name)).toBe('');
		}
	});
});
