<script lang="ts">
	/**
	 * Jarvis Code — schedule a coding job, and read what it did.
	 *
	 * The page has two halves and they are the two halves of the job: a form
	 * that starts one, and the branch, diff and checks it left behind. Progress
	 * in between is the same task machinery as everything else — the plan the
	 * model wrote becomes the steps, so the bar here is the bar on the phone.
	 *
	 * ## What this page does not decide
	 *
	 * Whether a repository may be changed, what commands may run, where a path
	 * may point. All three are jarvis-core's, all three are enforced there, and
	 * this page only reports them. A console that could widen any of them would
	 * be the hole the design exists to avoid — so `READ-ONLY` here is a label,
	 * not a switch.
	 *
	 * The forms' controls are raw `<input>`/`<select>`/`<textarea>` elements
	 * styled here: the labels and the e2e suite address them by id, and the
	 * library's `<Input>` has no id to give. START is the one primary control;
	 * creating and cloning are quiet, because a page with three filled buttons
	 * has no primary.
	 */
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import CodeDiff from '$lib/components/CodeDiff.svelte';
	import TaskCard from '$lib/components/TaskCard.svelte';
	import {
		describeChecks,
		describeEnvironment,
		describeRepo,
		describeSandbox,
		describeWorker,
		suggestedName,
		whyNoChecks,
		whyNotName,
		whyNotProject,
		whyNotStart,
		type CodeEnvironment,
		type CodeForge,
		type CodeRepo,
		type CodeResult,
		type CodeWorker
	} from '$lib/code';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { isUnsupported, type Subscription } from '$lib/jarvisClient';
	import { staggerStyle } from '$lib/motion';
	import { TASK_EVENTS, applyTaskEvent, mergeTaskList, type TaskRow } from '$lib/tasks';
	import { toasts } from '$lib/toast';
	import { Button, EmptyState, Panel, ScreenState, SkeletonRows } from '$lib/ui';

	let conn = $state<Connection | null>(null);
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');
	let loading = $state(true);
	let repos = $state<CodeRepo[]>([]);
	/** What will run a job (M101), from the server's listing. */
	let worker = $state<CodeWorker | undefined>(undefined);
	let sandboxed = $state(false);
	let environments = $state<CodeEnvironment[]>([]);
	let canCreate = $state(false);
	let workspace = $state('');
	let forges = $state<CodeForge[]>([]);
	// The new-repository form.
	let creating = $state(false);
	let newName = $state('');
	let newDescription = $state('');
	let newEnvironment = $state('');
	let saving = $state(false);
	// The clone form. Separate state, because a half-typed clone and a
	// half-typed new repository must not overwrite each other's fields.
	let cloning = $state(false);
	let cloneForge = $state('');
	let cloneProject = $state('');
	let cloneName = $state('');
	let cloneEnvironment = $state('');
	let jobs = $state<TaskRow[]>([]);
	let picked = $state('');
	let instruction = $state('');
	let starting = $state(false);
	let openJob = $state('');
	let result = $state<CodeResult | null>(null);
	let resultFor = $state('');
	let loadingResult = $state(false);

	const repo = $derived(repos.find((r) => r.name === picked) ?? null);
	const repoEnvironment = $derived(
		environments.find((e) => e.name === repo?.environment) ?? null
	);
	const nameProblem = $derived(newName ? whyNotName(newName) : '');
	const checksWithheld = $derived(repo ? whyNoChecks(repo, sandboxed) : '');
	const forge = $derived(forges.find((f) => f.name === cloneForge) ?? null);
	const projectProblem = $derived(cloneProject ? whyNotProject(forge, cloneProject) : '');
	// Empty means "use the last path segment", which is what git does; the
	// placeholder shows what that would be rather than leaving it a mystery.
	const cloneLocal = $derived(cloneName.trim() || suggestedName(cloneProject));
	const cloneNameProblem = $derived(cloneLocal ? whyNotName(cloneLocal) : '');
	const blocked = $derived(whyNotStart(repo, instruction));
	// Newest first, which is what `listing()` already answers with.
	const mine = $derived(jobs.filter((j) => j.kind === 'code'));

	// The dock and the task list both deep-link one job over here.
	$effect(() => {
		const focus = page.url.searchParams.get('job');
		if (focus && !openJob) openJob = focus;
	});

	// A job's result only exists once it is over, so this waits for the task to
	// finish rather than fetching on click and showing a 404 to somebody who
	// clicked a running job.
	$effect(() => {
		const id = openJob;
		const connection = conn;
		if (!id || !connection) return;
		const task = mine.find((j) => j.id === id);
		if (task && !task.finished) return;
		if (resultFor === id) return;
		void loadResult(connection, id);
	});

	async function loadResult(connection: Connection, id: string): Promise<void> {
		loadingResult = true;
		try {
			result = await connection.client.getCodeResult(id);
			resultFor = id;
		} catch (e) {
			err = describeError(e);
		} finally {
			loadingResult = false;
		}
	}

	async function start(): Promise<void> {
		if (!conn || starting || blocked) return;
		starting = true;
		err = '';
		try {
			const started = await conn.client.startCodeJob(picked, instruction.trim());
			toasts.success('Coding job started', started.title);
			// Cleared, because the next thing somebody types is a different job
			// and an instruction left in the box is one submitted twice.
			instruction = '';
			openJob = started.task_id;
			result = null;
			resultFor = '';
		} catch (e) {
			err = describeError(e);
			toasts.error('Could not start it', describeError(e));
		} finally {
			starting = false;
		}
	}

	/** Everything a create or clone response carries back. One place. */
	function absorb(listing: {
		repositories?: CodeRepo[];
		environments?: CodeEnvironment[];
		forges?: CodeForge[];
		worker?: CodeWorker;
	}): void {
		repos = listing.repositories ?? [];
		if (listing.worker) worker = listing.worker;
		environments = listing.environments ?? [];
		forges = listing.forges ?? forges;
	}

	async function cloneRepo(): Promise<void> {
		if (!conn || saving || projectProblem || cloneNameProblem || !cloneProject.trim()) return;
		saving = true;
		err = '';
		try {
			const listing = await conn.client.cloneCodeRepo({
				forge: cloneForge,
				project: cloneProject.trim(),
				name: cloneName.trim(),
				environment: cloneEnvironment
			});
			absorb(listing);
			toasts.success(`Cloned ${cloneLocal}`, `from ${cloneProject.trim()}`);
			picked = cloneLocal;
			cloneProject = '';
			cloneName = '';
			cloning = false;
		} catch (e) {
			err = describeError(e);
			toasts.error('Could not clone it', describeError(e));
		} finally {
			saving = false;
		}
	}

	async function createRepo(): Promise<void> {
		if (!conn || saving || nameProblem || !newName.trim()) return;
		saving = true;
		err = '';
		try {
			const listing = await conn.client.createCodeRepo({
				name: newName.trim(),
				description: newDescription.trim(),
				environment: newEnvironment
			});
			absorb(listing);
			toasts.success(`Created ${newName.trim()}`, 'it is empty — start a job to fill it');
			picked = newName.trim();
			newName = '';
			newDescription = '';
			creating = false;
		} catch (e) {
			err = describeError(e);
			toasts.error('Could not create it', describeError(e));
		} finally {
			saving = false;
		}
	}

	async function cancel(task: TaskRow): Promise<void> {
		if (!conn) return;
		try {
			const outcome = await conn.client.cancelTask(task.id);
			if (outcome.cancelled) {
				toasts.success(`Asked "${task.title}" to stop`, outcome.note ?? undefined);
			}
		} catch (e) {
			toasts.error('Could not cancel it', describeError(e));
		}
	}

	// --- the socket ----------------------------------------------------------
	let disposed = false;
	let subs: Subscription[] = [];
	let redialling = $state(false);
	let dial = 0;

	async function connect(): Promise<void> {
		if (redialling) return;
		redialling = true;
		const mineDial = ++dial;
		for (const sub of subs) void sub.unsubscribe();
		subs = [];
		conn?.close();
		conn = null;
		err = '';
		hint = '';
		loading = true;
		try {
			const connection = await openConnection({
				onStatus: (s) => {
					if (mineDial === dial) status = s;
				}
			});
			if (disposed || mineDial !== dial) {
				connection.close();
				return;
			}
			conn = connection;
			// Subscribe before listing, for the same reason as /tasks: a job that
			// moves between the two would be missed for as long as the tab is open.
			for (const name of TASK_EVENTS) {
				subs.push(
					await connection.client.subscribeEvents((event) => {
						jobs = applyTaskEvent(jobs, event);
					}, name)
				);
			}
			const listing = await connection.client.listCode();
			repos = listing.repositories ?? [];
			worker = listing.worker;
			sandboxed = !!listing.sandboxed;
			environments = listing.environments ?? [];
			forges = listing.forges ?? [];
			if (!cloneForge && forges.length) cloneForge = forges[0].name;
			canCreate = !!listing.can_create;
			workspace = listing.workspace ?? '';
			if (!picked && repos.length) picked = repos[0].name;
			jobs = mergeTaskList(jobs, await connection.client.listTasks({ kind: 'code' }));
		} catch (e) {
			if (isUnsupported(e)) {
				hint = 'this backend has no code integration — nothing here will fill in';
			} else {
				err = describeError(e);
			}
		} finally {
			redialling = false;
			if (!disposed) loading = false;
		}
	}

	onMount(() => {
		disposed = false;
		void connect();
		return () => {
			disposed = true;
			for (const sub of subs) void sub.unsubscribe();
			subs = [];
			conn?.close();
			conn = null;
		};
	});

	// The screen's status region. Loading and empty belong to the individual
	// lists below (this page has more than one); what is page-wide is the link
	// being down and the page's own failure, and `ScreenState` owns both.
	let screen = $derived<'ready' | 'error' | 'offline'>(
		status === 'closed' || status === 'error' ? 'offline' : err ? 'error' : 'ready'
	);
</script>

<p class="lede" data-testid="code-lede" data-redialling={redialling}>
	{repos.length} repositor{repos.length === 1 ? 'y' : 'ies'} · {mine.length} job{mine.length === 1
		? ''
		: 's'} · link {status}
</p>

<ScreenState
	status={screen}
	errorTitle="This page hit an error"
	errorDetail={err}
	onretry={connect}
	onreconnect={connect}
	busy={redialling}
	errorTestid="error"
/>

{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}

{#if loading}
	<SkeletonRows rows={3} />
{:else if !repos.length && !canCreate}
	<EmptyState
		testid="code-empty"
		title="No repositories"
		body="Jarvis works in repositories you name under `code:` in configuration.yaml. To let it create its own, set `code: workspace:` to a directory it may write in — then a NEW REPOSITORY button appears here."
	/>
{:else}
	<div class="stack">
		<!-- M82's second clause, M101: what will run a job — or why nothing will —
		     in one line, read off the orchestrator's /healthz by jarvis-core. -->
		<p class="hint" data-testid="code-worker" data-reachable={worker?.reachable ? 'true' : 'false'}>
			{describeWorker(worker)}
		</p>
		{#if canCreate}
			<Panel title="Repositories" meta="{repos.length} in the workspace" testid="code-repos">
				{#snippet children()}
					<p class="where" data-testid="code-workspace"><code>{workspace}</code></p>
					<div class="bar">
						<Button
							testid="code-new-repo"
							aria-expanded={creating}
							onclick={() => {
								creating = !creating;
								cloning = false;
								err = '';
							}}
						>
							{creating ? 'Cancel' : '+ New repository'}
						</Button>
						{#if forges.length}
							<Button
								testid="code-clone-repo"
								aria-expanded={cloning}
								onclick={() => {
									cloning = !cloning;
									creating = false;
									err = '';
								}}
							>
								{cloning ? 'Cancel' : '↓ Clone from a forge'}
							</Button>
						{/if}
					</div>

					{#if cloning}
						<div class="form" data-testid="code-clone-form">
							<label for="clone-forge">Forge</label>
							<select id="clone-forge" data-testid="clone-forge" bind:value={cloneForge}>
								{#each forges as f (f.name)}
									<option value={f.name}>{f.name} · {f.kind} · {f.host}</option>
								{/each}
							</select>
							{#if forge}
								<p class="hint" data-testid="clone-forge-note">
									{forge.allow.length
										? `Permitted: ${forge.allow.join(', ')}.`
										: 'Nothing is permitted on this forge yet — add it to `allow:` in configuration.yaml.'}
									{forge.push ? '' : ' Read-only: Jarvis cannot push branches back.'}
								</p>
								{#if !forge.has_token}
									<p class="hint" data-testid="clone-forge-token">
										No token configured. Public repositories clone fine; a private one will fail
										asking for a password.
									</p>
								{/if}
							{/if}

							<label for="clone-project">Repository</label>
							<input
								id="clone-project"
								type="text"
								placeholder="owner/repo"
								data-testid="clone-project"
								bind:value={cloneProject}
							/>
							{#if projectProblem}
								<p class="err" data-testid="clone-project-problem">{projectProblem}</p>
							{/if}

							<label for="clone-name">Call it (optional)</label>
							<input
								id="clone-name"
								type="text"
								placeholder={suggestedName(cloneProject) || 'the last part of the path'}
								data-testid="clone-name"
								bind:value={cloneName}
							/>
							{#if cloneNameProblem}
								<p class="err" data-testid="clone-name-problem">{cloneNameProblem}</p>
							{/if}

							<label for="clone-environment">Build environment</label>
							<select
								id="clone-environment"
								data-testid="clone-environment"
								bind:value={cloneEnvironment}
							>
								<option value="">None — no shell, declared checks only</option>
								{#each environments as e (e.name)}
									<option value={e.name}>{e.name} · {e.image}</option>
								{/each}
							</select>
							<p class="hint" data-testid="clone-environment-note">
								{describeEnvironment(environments.find((e) => e.name === cloneEnvironment) ?? null)}
							</p>

							<div class="row">
								<Button
									testid="clone-start"
									disabled={saving ||
										!!projectProblem ||
										!!cloneNameProblem ||
										!cloneProject.trim()}
									onclick={cloneRepo}
								>
									{saving ? 'Cloning…' : 'Clone'}
								</Button>
								<span class="hint">
									Cloned into {workspace}. Jarvis works on a `jarvis/…` branch and never pushes
									unless you ask it to.
								</span>
							</div>
						</div>
					{/if}

					{#if creating}
						<div class="form" data-testid="code-repo-form">
							<label for="repo-name">Name</label>
							<input
								id="repo-name"
								type="text"
								placeholder="snake-opengl"
								data-testid="repo-name"
								bind:value={newName}
							/>
							{#if nameProblem}
								<p class="err" data-testid="repo-name-problem">{nameProblem}</p>
							{/if}

							<label for="repo-description">What is it for</label>
							<input
								id="repo-description"
								type="text"
								data-testid="repo-description"
								bind:value={newDescription}
							/>

							<label for="repo-environment">Build environment</label>
							<select id="repo-environment" data-testid="repo-environment" bind:value={newEnvironment}>
								<option value="">None — no shell, declared checks only</option>
								{#each environments as e (e.name)}
									<option value={e.name}>{e.name} · {e.image}</option>
								{/each}
							</select>
							<p class="hint" data-testid="repo-environment-note">
								{describeEnvironment(environments.find((e) => e.name === newEnvironment) ?? null)}
							</p>

							<div class="row">
								<Button
									testid="repo-create"
									disabled={saving || !!nameProblem || !newName.trim()}
									onclick={createRepo}
								>
									{saving ? 'Creating…' : 'Create'}
								</Button>
								<span class="hint">
									Created in {workspace}, with a README and an initial commit. Jarvis never
									deletes a repository.
								</span>
							</div>
						</div>
					{/if}
				{/snippet}
			</Panel>
		{/if}

		<Panel title="New job" testid="code-new">
			{#snippet children()}
				<p class="where" data-testid="code-sandbox">{describeSandbox(sandboxed)}</p>
				<div class="form">
					<label for="code-repo">Repository</label>
					<select id="code-repo" bind:value={picked} data-testid="code-repo" disabled={!repos.length}>
						{#each repos as r (r.name)}
							<option value={r.name}>{r.name}</option>
						{/each}
					</select>
					{#if !repos.length}
						<!-- An empty picker above "Pick a repository first" is a dead end: it
						     says what is missing and not how to end up with one. -->
						<p class="notice" data-testid="code-no-repos">
							{#if canCreate}
								Nothing to work in yet. Use <strong>+ NEW REPOSITORY</strong> above to make
								one{#if forges.length}, or <strong>CLONE FROM A FORGE</strong> to pull one
									down{/if}.
							{:else}
								Nothing to work in, and creating is turned off. Declare a repository under
								`code: repositories:` in configuration.yaml, or remove `workspace: off` to let
								Jarvis make its own.
							{/if}
						</p>
					{/if}
					{#if repo}
						<p class="hint" data-testid="code-repo-note">
							{#if repo.description}{repo.description} — {/if}{describeRepo(repo, sandboxed)}
						</p>
						{#if repo.backend || repo.permission_mode || repo.origin}
							<!-- Sent by jarvis-core since M41 and shown nowhere until M99: which
							     worker, how free a hand it has, and where the code came from. -->
							<p class="hint" data-testid="code-repo-backend">
								{[
									repo.backend ? `worker: ${repo.backend}` : '',
									repo.permission_mode ? `permissions: ${repo.permission_mode}` : '',
									repo.origin ? `from ${repo.origin}` : ''
								]
									.filter(Boolean)
									.join(' · ')}
							</p>
						{/if}
						{#if checksWithheld}
							<p class="notice" data-testid="code-repo-no-checks">{checksWithheld}</p>
						{/if}
						<p
							class="hint"
							data-testid="code-repo-environment"
							data-networked={repo.networked ? 'true' : 'false'}
						>
							{describeEnvironment(repoEnvironment)}
						</p>
						{#if repo.networked}
							<p class="notice" data-testid="code-repo-egress">
								This environment can reach the internet. A job here can read this repository and
								make outbound connections.
							</p>
						{/if}
					{/if}

					<label for="code-instruction">What to change</label>
					<textarea
						id="code-instruction"
						rows="4"
						data-testid="code-instruction"
						placeholder="Say it in full — the job runs on its own and cannot ask you what you meant."
						bind:value={instruction}
					></textarea>

					<div class="row">
						<Button
							variant="primary"
							testid="code-start"
							disabled={!!blocked || starting}
							title={blocked || (starting ? 'Starting the job' : 'Start this coding job')}
							onclick={start}
						>
							{starting ? 'Starting…' : 'Start'}
						</Button>
						{#if blocked}<span class="hint" data-testid="code-blocked">{blocked}</span>{/if}
					</div>
					<p class="hint">
						Runs on a branch of its own — <code>jarvis/&lt;date&gt;-&lt;job&gt;</code> — and never
						commits to the one you are on. It refuses to start if the tree is dirty.
					</p>
				</div>
			{/snippet}
		</Panel>

		{#if !mine.length}
			<EmptyState testid="code-no-jobs" title="No jobs yet" body="A job you start here appears with its plan, its bar and, once it is over, its diff." />
		{:else}
			<section aria-labelledby="code-jobs-head">
				<h2 id="code-jobs-head">Jobs</h2>
				<div class="jobs jv-stagger" data-testid="code-jobs">
					{#each mine as job, i (job.id)}
						<div class="job" style={staggerStyle(i)} data-jv-row data-testid="job-{job.id}">
							<TaskCard task={job} onCancel={cancel} />
							<div class="opener">
								<Button
									testid="code-open-{job.id}"
									aria-expanded={openJob === job.id}
									onclick={() => {
										openJob = openJob === job.id ? '' : job.id;
										if (openJob !== resultFor) result = null;
									}}
								>
									{openJob === job.id ? 'Hide' : 'Diff & checks'}
								</Button>
							</div>

							{#if openJob === job.id}
								<div class="detail" data-testid="code-detail-{job.id}">
									{#if !job.finished}
										<p class="hint">Still running — the diff appears when it finishes.</p>
									{:else if loadingResult}
										<SkeletonRows rows={2} />
									{:else if !result}
										<p class="hint" data-testid="code-detail-gone">
											This job's diff is no longer held in memory. The branch it made is still
											in the repository.
										</p>
									{:else}
										<p class="branch" data-testid="code-branch">{result.branch || '(no branch)'}</p>
										{#if result.summary}<p class="summary">{result.summary}</p>{/if}
										{#if result.plan.length}
											<ol class="plan" data-testid="code-plan">
												{#each result.plan as step, s (s)}<li>{step}</li>{/each}
											</ol>
										{/if}
										{#if result.checks.length}
											<p class="checks" data-testid="code-checks">{describeChecks(result.checks)}</p>
											<ul class="checklist">
												{#each result.checks as check, c (c)}
													<li data-ok={check.ok}>
														<b>{check.command}</b>
														<span>{check.ok ? 'passed' : 'failed'}</span>
													</li>
												{/each}
											</ul>
										{/if}
										<CodeDiff diff={result.diff} stat={result.diff_stat} />
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			</section>
		{/if}
	</div>
{/if}

<style>
	.lede {
		margin: 0 0 var(--jv-space-4);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.stack {
		display: grid;
		gap: var(--jv-space-4);
	}
	h2 {
		margin: var(--jv-space-2) 0 var(--jv-space-3);
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	/* A sentence about the sandbox, or a path: the sentence is prose, the path is data. */
	.where {
		margin: 0 0 var(--jv-space-3);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.where code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
	}
	.bar {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
	}
	/* The forms. Labels in the chrome register; controls on the field ground. */
	.form {
		display: grid;
		gap: var(--jv-space-2);
		margin-top: var(--jv-space-3);
	}
	.form label {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		margin-top: var(--jv-space-2);
	}
	.form label:first-child {
		margin-top: 0;
	}
	.form input,
	.form select,
	.form textarea {
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-3);
		max-width: calc(var(--jv-space-7) * 12);
		transition: border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.form input:hover,
	.form select:hover,
	.form textarea:hover {
		border-color: var(--jv-line);
	}
	.form textarea {
		max-width: none;
		resize: vertical;
		line-height: 1.5;
	}
	.form input::placeholder,
	.form textarea::placeholder {
		color: var(--jv-text-faint);
	}
	.row {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--jv-space-3);
		margin-top: var(--jv-space-2);
	}
	.hint {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
		max-width: 70ch;
	}
	.hint code {
		font-family: var(--jv-font-chrome);
		color: var(--jv-text-dim);
	}
	.err {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-danger-text);
	}
	.notice {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-warn);
		max-width: 70ch;
	}
	.jobs {
		display: grid;
		gap: var(--jv-space-3);
	}
	.opener {
		display: flex;
		justify-content: flex-end;
		margin-top: var(--jv-space-2);
	}
	.detail {
		display: grid;
		gap: var(--jv-space-2);
		margin-top: var(--jv-space-2);
		padding: var(--jv-space-4);
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.branch {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-accent);
	}
	.summary {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
	}
	.plan {
		margin: 0;
		padding-left: 1.4em;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.checks {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.checklist {
		list-style: none;
		margin: 0;
		padding: 0;
		font-size: var(--jv-fs-xs);
	}
	.checklist li {
		display: flex;
		justify-content: space-between;
		gap: var(--jv-space-3);
		border-bottom: 1px solid var(--jv-line-hair);
		padding: var(--jv-space-2) 0;
		color: var(--jv-text-dim);
	}
	.checklist li:last-child {
		border-bottom: 0;
	}
	.checklist b {
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text);
	}
	.checklist li[data-ok='false'] span {
		color: var(--jv-danger-text);
	}
</style>
