<script lang="ts">
	/**
	 * What Jarvis will do later, and when.
	 *
	 * On `/tasks` rather than a page of its own: a scheduled job and the task it
	 * mints are the same thing at two moments, and putting them a navigation
	 * apart would make "did my seven o'clock reminder run?" a two-page question.
	 *
	 * The console may schedule a **service call**; the assistant's own tool
	 * cannot. That asymmetry is jarvis-core's and is enforced there — a request
	 * from here carried a bearer token, whereas a tool call may have been shaped
	 * by a page the model read. This form simply offers the third kind.
	 *
	 * All validation lives in `$lib/schedule.ts` and is tested in Node.
	 */
	import { Button } from '$lib/ui';
	import type { Connection } from '$lib/connection';
	import { describeError } from '$lib/connection';
	import { isUnsupported } from '$lib/jarvisClient';
	import { toasts } from '$lib/toast';
	import {
		DAYS,
		blankScheduleForm,
		describeJob,
		jobFootnote,
		parseScheduleForm,
		readOnlyNote,
		sortJobs,
		type ScheduleForm,
		type ScheduledJob
	} from '$lib/schedule';

	let { conn }: { conn: Connection | null } = $props();

	let jobs = $state<ScheduledJob[]>([]);
	let supported = $state(true);
	let loaded = $state(false);
	let err = $state('');
	let busy = $state('');
	let adding = $state(false);
	let form = $state<ScheduleForm>(blankScheduleForm());

	/**
	 * Recomputed on a timer, because "next in 12m" is a lie one minute later.
	 *
	 * A minute, not a second: the shortest thing this shows is "in under a
	 * minute", so anything faster would repaint for no visible change.
	 */
	let tick = $state(Date.now() / 1000);
	$effect(() => {
		const timer = setInterval(() => (tick = Date.now() / 1000), 60_000);
		return () => clearInterval(timer);
	});

	const ordered = $derived(sortJobs(jobs));

	async function refresh(connection: Connection): Promise<void> {
		try {
			jobs = await connection.client.listScheduled();
			supported = true;
		} catch (e) {
			// The versioning rule: an older jarvis-core has no scheduler, and the
			// panel is not drawn rather than showing a fault.
			if (isUnsupported(e)) supported = false;
			else err = describeError(e);
		} finally {
			loaded = true;
		}
	}

	$effect(() => {
		const connection = conn;
		if (!connection) return;
		void refresh(connection);
	});

	async function add(): Promise<void> {
		if (!conn || busy) return;
		err = '';
		const parsed = parseScheduleForm(form);
		if (!parsed.ok) {
			err = parsed.error;
			document.getElementById(`sched-${parsed.field}`)?.focus();
			return;
		}
		busy = 'add';
		try {
			const result = await conn.client.addScheduled(parsed.payload);
			jobs = [...jobs.filter((j) => j.id !== result.job.id), result.job];
			toasts.success(`Scheduled ${result.job.title}`, result.job.describes);
			form = blankScheduleForm();
			adding = false;
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	async function remove(job: ScheduledJob): Promise<void> {
		if (!conn || busy) return;
		busy = job.id;
		err = '';
		try {
			await conn.client.removeScheduled(job.id);
			jobs = jobs.filter((j) => j.id !== job.id);
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	async function toggle(job: ScheduledJob): Promise<void> {
		if (!conn || busy) return;
		busy = job.id;
		err = '';
		try {
			const result = await conn.client.setScheduledEnabled(job.id, !job.enabled);
			jobs = jobs.map((j) => (j.id === job.id ? result.job : j));
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	function toggleDay(day: string): void {
		form.days = form.days.includes(day)
			? form.days.filter((d) => d !== day)
			: [...form.days, day];
	}
</script>

{#if supported}
	<section class="panel" data-testid="schedule-panel">
		<div class="panel-head">
			<span>Scheduled</span>
			<span class="muted">{jobs.length} job{jobs.length === 1 ? '' : 's'}</span>
		</div>

		{#if err}<p class="err" role="alert" data-testid="schedule-error">{err}</p>{/if}

		{#if loaded && !jobs.length && !adding}
			<p class="muted" data-testid="schedule-empty">
				Nothing scheduled. Jarvis can set a reminder or a research run when you ask him to; a job
				that acts on the house is set up here.
			</p>
		{/if}

		<ul class="list">
			{#each ordered as job (job.id)}
				<li data-testid="sched-row-{job.id}" data-enabled={job.enabled} data-kind={job.kind}>
					<div class="row">
						<span class="name">
							<b>{job.title}</b>
							<span class="eid" data-testid="sched-when-{job.id}">{describeJob(job, tick)}</span>
						</span>
						<span class="acts">
							<span class="pill">{job.kind}</span>
							{#if job.editable}
								<Button testid="sched-toggle-{job.id}"
									disabled={!!busy}
									onclick={() => toggle(job)}
								>
									{job.enabled ? 'PAUSE' : 'RESUME'}
								</Button>
								<Button
									variant="danger"
									testid="sched-remove-{job.id}"
									disabled={!!busy}
									onclick={() => remove(job)}>REMOVE</Button
								>
							{:else}
								<span class="eid" data-testid="sched-readonly-{job.id}">{readOnlyNote(job)}</span>
							{/if}
						</span>
					</div>
					{#if jobFootnote(job)}
						<p class="note" data-testid="sched-note-{job.id}">{jobFootnote(job)}</p>
					{/if}
				</li>
			{/each}
		</ul>

		<div class="toolbar">
			<Button
				variant="primary"
				testid="sched-new"
				aria-expanded={adding}
				onclick={() => {
					adding = !adding;
					err = '';
				}}
			>
				{adding ? 'CANCEL' : '+ SCHEDULE SOMETHING'}
			</Button>
		</div>

		{#if adding}
			<div class="editor" data-testid="schedule-editor">
				<label for="sched-kind">What</label>
				<select id="sched-kind" bind:value={form.kind} data-testid="sched-kind">
					<option value="notify">Say something</option>
					<option value="research">Research a question</option>
					<option value="code">Run a coding job</option>
					<option value="service">Call a service</option>
				</select>

				{#if form.kind === 'notify'}
					<label for="sched-message">Message</label>
					<input id="sched-message" type="text" bind:value={form.message} />
				{:else if form.kind === 'research'}
					<label for="sched-question">Question</label>
					<input id="sched-question" type="text" bind:value={form.question} />
				{:else if form.kind === 'code'}
					<label for="sched-repo">Repository</label>
					<input id="sched-repo" type="text" bind:value={form.repo} data-testid="sched-repo" />
					<label for="sched-instruction">What to change</label>
					<input
						id="sched-instruction"
						type="text"
						bind:value={form.instruction}
						data-testid="sched-instruction"
					/>
					<p class="hint">
						Runs on a branch of its own and leaves you a diff. The assistant cannot schedule one of
						these — starting a coding job asks a human, and a timer must not be the way round that.
					</p>
				{:else}
					<label for="sched-service">Service</label>
					<input id="sched-service" type="text" bind:value={form.service} placeholder="light.turn_on" />
					<p class="hint">
						Goes through the same approval as an automation would: a service that needs a yes still
						gets asked.
					</p>
				{/if}

				<label for="sched-title">Name (optional)</label>
				<input id="sched-title" type="text" bind:value={form.title} />

				<label for="sched-mode">When</label>
				<select id="sched-mode" bind:value={form.mode} data-testid="sched-mode">
					<option value="once">Once</option>
					<option value="daily">Every day</option>
					<option value="weekly">On certain days</option>
					<option value="every">Every N minutes</option>
				</select>

				{#if form.mode === 'every'}
					<label for="sched-minutes">Minutes</label>
					<input id="sched-minutes" type="number" min="5" bind:value={form.minutes} />
				{:else}
					{#if form.mode === 'once'}
						<label for="sched-date">Date</label>
						<input id="sched-date" type="date" bind:value={form.date} />
					{/if}
					<label for="sched-time">Time</label>
					<input id="sched-time" type="time" bind:value={form.time} />
					{#if form.mode === 'weekly'}
						<span class="days" id="sched-days" data-testid="sched-days">
							{#each DAYS as day (day)}
								<Button
									pressed={form.days.includes(day)}
									testid="sched-day-{day}"
									onclick={() => toggleDay(day)}>{day.toUpperCase()}</Button
								>
							{/each}
						</span>
					{/if}
				{/if}

				<div class="row">
					<Button
						variant="primary"
						testid="sched-save"
						disabled={busy === 'add'}
						onclick={add}
					>
						{busy === 'add' ? 'SAVING…' : 'SCHEDULE'}
					</Button>
				</div>
			</div>
		{/if}
	</section>
{/if}

<style>
	/* `.panel`, `.row`, `.btn`, `.name`, `.eid`, `.muted`, `.err`, `.editor` and
	   `.pill` all come from chrome.css. */
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.list > li {
		border-bottom: 1px dashed var(--jv-line-hair);
	}
	.list > li:last-child {
		border-bottom: 0;
	}
	li[data-enabled='false'] .name b {
		color: var(--jv-text-faint);
		text-decoration: line-through;
	}
	.acts {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
	}
	.note {
		margin: 0 0 var(--jv-space-2);
		font-size: var(--jv-fs-xs);
		color: var(--jv-warn);
	}
	.hint {
		margin: 0 0 var(--jv-space-2);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.days {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-1);
		margin-bottom: var(--jv-space-2);
	}
	.days .btn.on {
		color: var(--jv-accent);
		border-color: var(--jv-accent-deep);
	}
	.toolbar {
		display: flex;
		gap: var(--jv-space-2);
		margin-top: var(--jv-space-2);
	}
</style>
