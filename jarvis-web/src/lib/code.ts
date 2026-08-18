/**
 * Jarvis Code, as the console understands it.
 *
 * jarvis-core owns the job: the plan, the branch, the diff, the checks. What
 * is here is the reading of it — the shape of a diff for the screen, and the
 * two sentences a person needs before they press START.
 *
 * ## Why the diff is parsed here and not rendered as text
 *
 * A diff in a `<pre>` is a wall of monochrome that hides the one `-` line that
 * matters. Splitting it into typed lines is what lets added and removed rows
 * carry a colour, and it costs one pass over a string that is already capped
 * at 400 kB by jarvis-core.
 *
 * Nothing here decides whether a job may run. A read-only repository is a
 * server-side fact — the model is not even offered the edit tools — and this
 * file only says so on screen.
 */

export interface CodeRepo {
	name: string;
	path: string;
	description: string;
	checks: string[];
	writable: boolean;
}

export interface CodeCheck {
	command: string;
	ok: boolean;
	output: string;
}

export interface CodeTrailEntry {
	tool: string;
	args: string;
	outcome: string;
}

export interface CodeResult {
	repo: string;
	instruction: string;
	branch: string;
	plan: string[];
	files_changed: string[];
	diff_stat: string;
	diff: string;
	checks: CodeCheck[];
	trail: CodeTrailEntry[];
	summary: string;
	rounds: number;
}

export interface CodeListing {
	repositories: CodeRepo[];
	jobs: unknown[];
	sandboxed: boolean;
}

export type DiffKind = 'add' | 'remove' | 'meta' | 'hunk' | 'context';

export interface DiffLine {
	kind: DiffKind;
	text: string;
}

/** Longer than anybody reads on a page; the rest is one line saying so. */
export const MAX_DIFF_LINES = 1200;

/**
 * A unified diff, split into lines that can be coloured.
 *
 * `---`/`+++` are checked BEFORE `-`/`+`, which is the whole subtlety: a file
 * header starts with the same character as a removed line, and getting that
 * backwards paints every header red and makes an added file look deleted.
 */
export function parseDiff(diff: string): DiffLine[] {
	const out: DiffLine[] = [];
	if (!diff) return out;
	for (const text of diff.split('\n')) {
		if (out.length >= MAX_DIFF_LINES) {
			out.push({ kind: 'meta', text: `… diff truncated at ${MAX_DIFF_LINES} lines` });
			break;
		}
		if (
			text.startsWith('diff ') ||
			text.startsWith('index ') ||
			text.startsWith('--- ') ||
			text.startsWith('+++ ') ||
			text.startsWith('new file') ||
			text.startsWith('deleted file') ||
			text.startsWith('similarity ') ||
			text.startsWith('rename ')
		) {
			out.push({ kind: 'meta', text });
		} else if (text.startsWith('@@')) {
			out.push({ kind: 'hunk', text });
		} else if (text.startsWith('+')) {
			out.push({ kind: 'add', text });
		} else if (text.startsWith('-')) {
			out.push({ kind: 'remove', text });
		} else {
			out.push({ kind: 'context', text });
		}
	}
	return out;
}

/** "+12 −3", from the parsed lines rather than from git's own stat line. */
export function countChanges(lines: DiffLine[]): { added: number; removed: number } {
	let added = 0;
	let removed = 0;
	for (const line of lines) {
		if (line.kind === 'add') added += 1;
		else if (line.kind === 'remove') removed += 1;
	}
	return { added, removed };
}

/**
 * What a repository can have done to it, in one sentence.
 *
 * The read-only case is the one worth wording carefully: somebody who types an
 * instruction into a read-only repository and gets a report back instead of a
 * branch should have known before they pressed the button, not after.
 */
export function describeRepo(repo: CodeRepo): string {
	const parts: string[] = [];
	parts.push(repo.writable ? 'may be changed' : 'read-only — Jarvis will look, not touch');
	if (repo.checks.length) {
		parts.push(`checks: ${repo.checks.join(', ')}`);
	} else {
		parts.push('no checks configured');
	}
	return parts.join(' · ');
}

/**
 * Whether a job may be started, and why not.
 *
 * Returns an empty string when it may. The caller shows the string, so it is
 * written as a sentence to a person rather than as a validation code.
 */
export function whyNotStart(repo: CodeRepo | null, instruction: string): string {
	if (!repo) return 'Pick a repository first.';
	if (!instruction.trim()) return 'Say what to change.';
	if (instruction.trim().length < 8) {
		// Not arbitrary: a job cannot ask a follow-up question, so a
		// three-word instruction produces three minutes of guessing.
		return 'Say a bit more — the job cannot ask you what you meant.';
	}
	return '';
}

/** `checks: 2/3 passed`, or nothing when a job ran none. */
export function describeChecks(checks: CodeCheck[]): string {
	if (!checks.length) return '';
	const passed = checks.filter((c) => c.ok).length;
	return `${passed}/${checks.length} check${checks.length === 1 ? '' : 's'} passed`;
}

/**
 * The sandbox line, worded so it cannot be read as a promise.
 *
 * An operator with no wrapper configured is not "unsandboxed" in the sense
 * that matters — the model still has no shell and cannot leave the repository
 * — so the sentence names the one thing that changes: where the repository's
 * own check commands run.
 */
export function describeSandbox(sandboxed: boolean): string {
	return sandboxed
		? 'Checks run behind the wrapper set in configuration.yaml.'
		: 'Checks run as the server does. Set `code.sandbox` to put a wrapper around them.';
}
