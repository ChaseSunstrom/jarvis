/**
 * The diff reader, and the two sentences on the Code page.
 *
 * The header case below is the one that matters: `---` and `+++` start with
 * the same characters as a removed and an added line, so a naive reader paints
 * every file header as a deletion and an added file looks like a removed one.
 */
import { describe, expect, it } from 'vitest';
import {
	MAX_DIFF_LINES,
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
		expect(describeRepo(repo({ checks: ['pytest -q'] }))).toContain('pytest -q');
		expect(describeRepo(repo())).toContain('no checks');
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
		expect(off).toContain('Checks run as the server does');
		expect(off).not.toMatch(/not sandboxed|unsafe/i);
	});

	it('says so when there is one', () => {
		expect(describeSandbox(true)).toContain('wrapper');
	});
});
