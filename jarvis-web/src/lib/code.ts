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
	/** The name of a `code: environments:` entry, or "" for no shell at all. */
	environment?: string;
	/** True when Jarvis created it, false when the operator declared it. */
	managed?: boolean;
	/** One line from jarvis-core describing the environment, or "". */
	environment_detail?: string;
	/** Whether that environment can reach the internet. */
	networked?: boolean;
	/** Which coding backend runs the jobs ("opencode", "claude"), from jarvis-core. */
	backend?: string;
	/** How far that backend may act without asking, in its own words. */
	permission_mode?: string;
	/** Where the repository came from, when jarvis-core cloned it. */
	origin?: string;
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
	/** Commits the job made on its own branch, newest last. */
	commits?: CodeCommit[];
	/** Every action a human was asked about, and what they said. */
	approvals?: CodeApproval[];
	/** True when the repository's own check passed at the end of the job. */
	verified?: boolean;
	summary: string;
	rounds: number;
}

/** One commit on the job's branch. */
export interface CodeCommit {
	sha: string;
	message: string;
	stat: string;
	files: number;
}

/** One held action, and the answer it got. */
export interface CodeApproval {
	/** `edit`, `check` or `command`. */
	kind: string;
	summary: string;
	approved: boolean;
}

export interface CodeEnvironment {
	name: string;
	image: string;
	/** `none` or `egress`. */
	network: string;
	memory: string;
	cpus: string;
	/** Variable NAMES only — jarvis-core never sends the values. */
	env: string[];
	setup: string[];
}

export interface CodeForge {
	name: string;
	/** `github` or `gitlab`. */
	kind: string;
	host: string;
	/** Whether a token is configured. jarvis-core NEVER sends the value. */
	has_token: boolean;
	/** The allow-list, verbatim: `owner/repo` or `owner/*`. */
	allow: string[];
	/** Whether Jarvis may push its branches back. */
	push: boolean;
}

export interface CodeListing {
	repositories: CodeRepo[];
	jobs: unknown[];
	sandboxed: boolean;
	environments: CodeEnvironment[];
	forges: CodeForge[];
	/** Whether `code: workspace:` is set, i.e. whether creation is possible. */
	can_create: boolean;
	workspace: string;
	/** The coding worker jarvis-core hands jobs to (M101), or absent on a house without one. */
	worker?: CodeWorker;
}

/** What `jarvis/code/list` says about the worker, from the orchestrator's `/healthz`. */
export interface CodeWorker {
	/** False when no `orchestrator:` is configured. */
	enabled: boolean;
	url: string;
	/** Whether `/healthz` answered on the last probe. */
	reachable: boolean;
	backend: string;
	/** The binary's version as the orchestrator reports it, or "" when it has none. */
	version: string;
	planner_model: string;
	coder_model: string;
	/** Why it is not reachable, in one line, or "". */
	error: string;
	checked_at: number;
}

/** One line for the Code screen: what will run a job, or why nothing will. */
export function describeWorker(worker: CodeWorker | undefined): string {
	if (!worker || !worker.enabled) return 'No coding worker: `orchestrator:` is not configured, so a job has nowhere to run.';
	if (!worker.reachable) return `The orchestrator at ${worker.url} is not answering${worker.error ? ` — ${worker.error}` : ''}.`;
	const binary = worker.version ? `${worker.backend} ${worker.version}` : `${worker.backend} is not installed in the orchestrator`;
	const models = [worker.coder_model && `coder ${worker.coder_model}`, worker.planner_model && `planner ${worker.planner_model}`]
		.filter(Boolean)
		.join(', ');
	return `Worker: ${binary}${models ? ` · ${models}` : ''}.`;
}

/**
 * Whether a repository name is usable, as a sentence or "".
 *
 * A deliberate copy of `check_name` in jarvis-core, and the server still
 * refuses independently — this exists so the form can say why BEFORE a round
 * trip, not so the browser can decide. The two rules are pinned together by
 * `test_the_console_and_the_server_agree_about_names`.
 */
export function whyNotName(name: string): string {
	const text = name.trim();
	if (!text) return 'A repository needs a name.';
	if (text.length > 64) return 'That name is too long — 64 characters at most.';
	if (text !== text.toLowerCase()) {
		return 'Use lowercase: a name is a directory, and some filesystems do not tell “Foo” from “foo”.';
	}
	if (!/^[a-z0-9][a-z0-9._-]*$/.test(text)) {
		return 'Use lowercase letters, digits, dot, dash and underscore, starting with a letter or digit. No spaces or slashes.';
	}
	if (text.includes('..')) return 'A name may not contain “..”.';
	if (RESERVED_NAMES.has(text)) {
		return `“${text}” is reserved — it means something else to git or the filesystem.`;
	}
	return '';
}

/**
 * Whether this forge's allow-list covers `project`, as a sentence or "".
 *
 * A deliberate copy of `permits()` in jarvis-core's `forges.py`, for the same
 * reason as `whyNotName`: the server refuses independently and is the only
 * thing that decides, but "owner/repo is not on this forge's list" is a fact
 * the form already has, and making somebody press a button to hear it is
 * making them wait for an answer that was always available.
 *
 * The two are pinned together by
 * `test_the_console_and_the_server_agree_about_the_allow_list`.
 */
export function whyNotProject(forge: CodeForge | null, project: string): string {
	const text = project.trim();
	if (!forge) return 'Pick a forge first.';
	if (!text) return 'Which repository? Give it as owner/repo.';
	const parts = splitProject(text);
	if (!parts.length) {
		return 'Give it as owner/repo — no leading or trailing slash, no scheme, no “..”.';
	}
	// A missing token is NOT judged here. A public clone works without one, and
	// the form says so under the picker; putting it in this function once made
	// a tokenless forge return "permitted" before the allow-list ran, which is
	// the one thing this function exists to decide.
	if (!permits(forge, parts)) {
		const list = forge.allow.length ? forge.allow.join(', ') : 'nothing yet';
		return `${text} is not on ${forge.name}’s allow-list. Permitted: ${list}.`;
	}
	return '';
}

/**
 * `owner/name` into its segments, or [] if it is not one. Mirrors
 * `split_project()`.
 *
 * A leading slash is REFUSED rather than stripped, which is what jarvis-core
 * does and for its reason: `/etc/passwd` trimmed to `etc/passwd` is a
 * perfectly well-formed project path, and a form that quietly repairs a
 * mistake is a form that accepts what the server will reject.
 */
function splitProject(project: string): string[] {
	const text = project.trim();
	if (!text || text.startsWith('/') || text.endsWith('/')) return [];
	if (text.includes('..') || text.includes(':') || text.includes('\\')) return [];
	const parts = text.split('/');
	if (parts.length < 2 || parts.length > 8) return [];
	if (!parts.every((part) => /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(part))) return [];
	return parts;
}

/** Case-insensitive, and `owner/*` matches a whole owner. Mirrors `permits()`. */
function permits(forge: CodeForge, parts: string[]): boolean {
	const wanted = parts.map((p) => p.toLowerCase());
	return forge.allow.some((pattern) => {
		const segments = pattern.split('/').map((p) => p.toLowerCase());
		if (segments.length && segments[segments.length - 1] === '*') {
			const head = segments.slice(0, -1);
			return (
				wanted.length > head.length &&
				head.every((segment, index) => wanted[index] === segment)
			);
		}
		return (
			segments.length === wanted.length &&
			segments.every((segment, index) => wanted[index] === segment)
		);
	});
}

/**
 * The local name a cloned repository gets if you do not choose one.
 *
 * The last path segment, which is what `git clone` itself would do, lowercased
 * because `whyNotName` refuses capitals and a suggestion the form then rejects
 * is worse than no suggestion.
 */
export function suggestedName(project: string): string {
	const parts = project.trim().split('/').filter(Boolean);
	const last = (parts[parts.length - 1] || '').toLowerCase();
	return last.replace(/\.git$/, '');
}

/** Mirrors `_RESERVED` in jarvis-core's `repos.py`. */
export const RESERVED_NAMES = new Set([
	'con', 'prn', 'aux', 'nul', 'com1', 'lpt1',
	'git', '.git', 'node_modules', '__pycache__', 'venv', '.venv',
	'tmp', 'temp', 'test', 'dist', 'build'
]);

/**
 * What an environment lets a job do, in words a person can weigh.
 *
 * The network line is the one that matters: `egress` means the container can
 * read this repository AND make outbound connections. Saying that plainly is
 * the difference between an informed choice and a default nobody read.
 */
export function describeEnvironment(environment: CodeEnvironment | null): string {
	if (!environment) {
		// Deliberately not "and runs the declared checks": whether it may do
		// that depends on the repository, and `whyNoChecks` is what knows.
		return 'No environment — it can read and edit. No shell.';
	}
	const reach =
		environment.network === 'egress'
			? 'can reach the internet, so it can install what it needs'
			: 'has no network';
	return `${environment.image} · ${reach} · ${environment.memory} RAM`;
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
export function describeRepo(repo: CodeRepo, sandboxed = false): string {
	const parts: string[] = [];
	parts.push(repo.writable ? 'may be changed' : 'read-only — Jarvis will look, not touch');
	if (!repo.checks.length) {
		parts.push('no checks configured');
	} else if (whyNoChecks(repo, sandboxed)) {
		parts.push('checks withheld');
	} else {
		parts.push(`checks: ${repo.checks.join(', ')}`);
	}
	return parts.join(' · ');
}

/**
 * Why this repository's checks will not run, or "".
 *
 * A check is the operator's command string, but it executes FILES out of the
 * working tree — `pytest` imports `conftest.py`, `npm test` runs
 * `package.json`, `make` runs the Makefile — and on a writable repository a
 * job can write those. So jarvis-core withholds `run_check` entirely unless
 * there is an `environment:` or a `sandbox:` wrapper between the check and the
 * machine. Saying so here means the operator finds out from the page rather
 * than from a job that quietly never ran its tests.
 */
export function whyNoChecks(repo: CodeRepo, sandboxed = false): string {
	if (!repo.checks.length || !repo.writable) return '';
	if (repo.environment || sandboxed) return '';
	return (
		'Checks are withheld here: this repository can be changed, and a check runs ' +
		'files a job could have written. Give it an environment, or set `code.sandbox`, ' +
		'or make it read-only.'
	);
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
		: 'Checks run in the repository’s environment, or not at all on a writable ' +
			'repository without one. Set `code.sandbox` for a wrapper instead.';
}
