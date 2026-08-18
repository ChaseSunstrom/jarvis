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
	 */
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import CodeDiff from '$lib/components/CodeDiff.svelte';
	import Reconnect from '$lib/components/Reconnect.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import TaskCard from '$lib/components/TaskCard.svelte';
	import {
		describeChecks,
		describeRepo,
		describeSandbox,
		whyNotStart,
		type CodeRepo,
		type CodeResult
	} from '$lib/code';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { isUnsupported, type Subscription } from '$lib/jarvisClient';
	import { staggerStyle } from '$lib/motion';
	import { TASK_EVENTS, applyTaskEvent, mergeTaskList, type TaskRow } from '$lib/tasks';
	import { toasts } from '$lib/toast';

	let conn = $state<Connection | null>(null);
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');
	let loading = $state(true);
	let repos = $state<CodeRepo[]>([]);
	let sandboxed = $state(false);
	let jobs = $state<TaskRow[]>([]);
	let picked = $state('');
	let instruction = $state('');
	let starting = $state(false);
	let openJob = $state('');
	let result = $state<CodeResult | null>(null);
	let resultFor = $state('');
	let loadingResult = $state(false);

	const repo = $derived(repos.find((r) => r.name === picked) ?? null);
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
			sandboxed = !!listing.sandboxed;
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
</script>

<svelte:head><title>Jarvis · Code</title></svelte:head>

<h1>CODE</h1>
<p class="lede" data-testid="code-lede" data-redialling={redialling}>
	{repos.length} repositor{repos.length === 1 ? 'y' : 'ies'} · {mine.length} job{mine.length === 1
		? ''
		: 's'} · link {status}
</p>

<Reconnect {status} busy={redialling} retry={connect} />

{#if err}<p class="err" data-testid="error" role="alert">{err}</p>{/if}
{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}

{#if loading}
	<Skeleton rows={3} />
{:else if !repos.length}
	<div class="jv-empty" data-testid="code-empty">
		<span class="jv-empty-mark" aria-hidden="true">[ ]</span>
		<p class="jv-empty-title">No repositories</p>
		<p class="jv-empty-body">
			Jarvis works only in repositories you name, under <code>code:</code> in
			<code>configuration.yaml</code>. Each one says whether it may be changed and which commands
			may be run in it — there is nothing to add from here, deliberately.
		</p>
	</div>
{:else}
	<section class="panel" data-testid="code-new">
		<div class="panel-head">
			<span>New job</span>
			<span class="muted" data-testid="code-sandbox">{describeSandbox(sandboxed)}</span>
		</div>

		<label for="code-repo">Repository</label>
		<select id="code-repo" bind:value={picked} data-testid="code-repo">
			{#each repos as r (r.name)}
				<option value={r.name}>{r.name}</option>
			{/each}
		</select>
		{#if repo}
			<p class="hint" data-testid="code-repo-note">
				{#if repo.description}{repo.description} — {/if}{describeRepo(repo)}
			</p>
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
			<button
				type="button"
				class="btn"
				data-testid="code-start"
				disabled={!!blocked || starting}
				onclick={start}
			>
				{starting ? 'STARTING…' : 'START'}
			</button>
			{#if blocked}<span class="hint" data-testid="code-blocked">{blocked}</span>{/if}
		</div>
		<p class="hint">
			Runs on a branch of its own — <code>jarvis/&lt;date&gt;-&lt;job&gt;</code> — and never
			commits to the one you are on. It refuses to start if the tree is dirty.
		</p>
	</section>

	{#if !mine.length}
		<div class="jv-empty" data-testid="code-no-jobs">
			<span class="jv-empty-mark" aria-hidden="true">[ ]</span>
			<p class="jv-empty-title">No jobs yet</p>
		</div>
	{:else}
		<h2>JOBS</h2>
		<div class="stack jv-stagger" data-testid="code-jobs">
			{#each mine as job, i (job.id)}
				<div style={staggerStyle(i)}>
					<TaskCard task={job} onCancel={cancel} />
					<div class="opener">
						<button
							type="button"
							class="btn ghost"
							data-testid="code-open-{job.id}"
							aria-expanded={openJob === job.id}
							onclick={() => {
								openJob = openJob === job.id ? '' : job.id;
								if (openJob !== resultFor) result = null;
							}}
						>
							{openJob === job.id ? 'HIDE' : 'DIFF & CHECKS'}
						</button>
					</div>

					{#if openJob === job.id}
						<div class="detail" data-testid="code-detail-{job.id}">
							{#if !job.finished}
								<p class="muted">Still running — the diff appears when it finishes.</p>
							{:else if loadingResult}
								<Skeleton rows={2} />
							{:else if !result}
								<p class="muted" data-testid="code-detail-gone">
									This job's diff is no longer held in memory. The branch it made is still in the
									repository.
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
	{/if}
{/if}

<style>
	h1 {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-lg);
		letter-spacing: var(--jv-track-logo);
		color: var(--jv-text-bright);
		margin: 0 0 var(--jv-space-1);
	}
	h2 {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-faint);
		margin: var(--jv-space-4) 0 var(--jv-space-2);
	}
	.lede {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		margin: 0 0 var(--jv-space-3);
	}
	.stack {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-3);
	}
	.opener {
		display: flex;
		justify-content: flex-end;
		margin-top: calc(-1 * var(--jv-space-1));
	}
	.detail {
		border: 1px solid var(--jv-line-hair);
		border-top: 0;
		padding: var(--jv-space-2);
	}
	.branch {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-accent);
		margin: 0 0 var(--jv-space-1);
	}
	.summary {
		margin: 0 0 var(--jv-space-2);
		font-size: var(--jv-fs-sm);
	}
	.plan {
		margin: 0 0 var(--jv-space-2);
		padding-left: 1.4em;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.checks {
		margin: 0 0 var(--jv-space-1);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.checklist {
		list-style: none;
		margin: 0 0 var(--jv-space-2);
		padding: 0;
		font-size: var(--jv-fs-xs);
	}
	.checklist li {
		display: flex;
		justify-content: space-between;
		gap: var(--jv-space-2);
		border-bottom: 1px dashed var(--jv-line-hair);
		padding: 2px 0;
	}
	.checklist li[data-ok='false'] span {
		color: var(--jv-danger-text);
	}
	.hint {
		margin: 0 0 var(--jv-space-2);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.err {
		color: var(--jv-danger-text);
		font-size: var(--jv-fs-xs);
		margin: 0 0 var(--jv-space-2);
	}
	.notice {
		color: var(--jv-warn);
		font-size: var(--jv-fs-xs);
		margin: 0 0 var(--jv-space-2);
	}
	textarea {
		width: 100%;
		resize: vertical;
	}
</style>
