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
	 *
	 * The form's controls are raw `<input>`/`<select>` elements styled here:
	 * the e2e suite and the labels address them by id, and the library's
	 * `<Input>` has no id to give.
	 */
	import { Button, Panel, Pill } from '$lib/ui';
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

	let {
		conn,
		onJobs
	}: {
		conn: Connection | null;
		/** The list, whenever it changes — the page draws the day from it. */
		onJobs?: (jobs: ScheduledJob[]) => void;
	} = $props();

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
	$effect(() => {
		onJobs?.(jobs);
	});

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
		// Live (M99): a firing moves `next_at`, `last_result` and `missed`,
		// and the panel used to show the old ones until a reload.
		let sub: { unsubscribe: () => Promise<void> } | null = null;
		let gone = false;
		void connection.client
			.subscribeEvents(() => void refresh(connection), 'jarvis_schedule_fired')
			.then((s) => {
				if (gone) void s.unsubscribe();
				else sub = s;
			})
			.catch(() => {
				// An older server without the event still lists on demand.
			});
		return () => {
			gone = true;
			void sub?.unsubscribe();
		};
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
	<Panel title="Scheduled" meta="{jobs.length} job{jobs.length === 1 ? '' : 's'}" testid="schedule-panel">
		{#snippet children()}
			{#if err}<p class="err" role="alert" data-testid="schedule-error">{err}</p>{/if}

			{#if loaded && !jobs.length && !adding}
				<p class="none" data-testid="schedule-empty">
					Nothing scheduled. Jarvis can set a reminder or a research run when you ask him to; a
					job that acts on the house is set up here.
				</p>
			{/if}

			<ul class="list">
				{#each ordered as job (job.id)}
					<li data-testid="sched-row-{job.id}" data-enabled={job.enabled} data-kind={job.kind}>
						<div class="row">
							<span class="name">
								<b>{job.title}</b>
								<span class="when" data-testid="sched-when-{job.id}">{describeJob(job, tick)}</span>
							</span>
							<span class="acts">
								<Pill>{job.kind}</Pill>
								{#if job.editable}
									<Button testid="sched-toggle-{job.id}" disabled={!!busy} onclick={() => toggle(job)}>
										{job.enabled ? 'Pause' : 'Resume'}
									</Button>
									<Button
										variant="danger"
										testid="sched-remove-{job.id}"
										disabled={!!busy}
										onclick={() => remove(job)}>Remove</Button
									>
								{:else}
									<span class="readonly" data-testid="sched-readonly-{job.id}">{readOnlyNote(job)}</span>
								{/if}
							</span>
						</div>
						{#if jobFootnote(job)}
							<p class="note" data-testid="sched-note-{job.id}">{jobFootnote(job)}</p>
						{/if}
					</li>
				{/each}
			</ul>

			<div class="foot">
				{#if adding}
					<Button
						testid="sched-new"
						aria-expanded={adding}
						onclick={() => {
							adding = false;
							err = '';
						}}
					>
						Cancel
					</Button>
				{:else}
					<Button
						variant="primary"
						testid="sched-new"
						aria-expanded={adding}
						onclick={() => {
							adding = true;
							err = '';
						}}
					>
						+ Schedule something
					</Button>
				{/if}
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
							Runs on a branch of its own and leaves you a diff. The assistant cannot schedule one
							of these — starting a coding job asks a human, and a timer must not be the way round
							that.
						</p>
					{:else}
						<label for="sched-service">Service</label>
						<input id="sched-service" type="text" bind:value={form.service} placeholder="light.turn_on" />
						<p class="hint">
							Goes through the same approval as an automation would: a service that needs a yes
							still gets asked.
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

					<div class="save">
						<Button variant="primary" testid="sched-save" disabled={busy === 'add'} onclick={add}>
							{busy === 'add' ? 'Saving…' : 'Schedule'}
						</Button>
					</div>
				</div>
			{/if}
		{/snippet}
	</Panel>
{/if}

<style>
	.err {
		margin: 0 0 var(--jv-space-3);
		font-size: var(--jv-fs-xs);
		color: var(--jv-danger-text);
	}
	.none {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-faint);
	}
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.list > li {
		border-bottom: 1px solid var(--jv-line-hair);
		padding: var(--jv-space-3) 0;
	}
	.list > li:first-child {
		padding-top: 0;
	}
	.list > li:last-child {
		border-bottom: 0;
	}
	.row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
	}
	.name {
		display: grid;
		gap: var(--jv-space-1);
		min-width: 0;
		flex: 1 1 14rem;
	}
	.name b {
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
	}
	li[data-enabled='false'] .name b {
		color: var(--jv-text-faint);
		text-decoration: line-through;
	}
	.when,
	.readonly {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.acts {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
	}
	.note {
		margin: var(--jv-space-2) 0 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-warn);
	}
	.foot {
		display: flex;
		gap: var(--jv-space-2);
		margin-top: var(--jv-space-4);
	}
	.editor {
		display: grid;
		gap: var(--jv-space-2);
		margin-top: var(--jv-space-4);
		padding: var(--jv-space-4);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		background: var(--jv-bg-raised);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.editor label {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		margin-top: var(--jv-space-2);
	}
	.editor label:first-child {
		margin-top: 0;
	}
	.editor input,
	.editor select {
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-3);
		max-width: calc(var(--jv-space-7) * 10);
		transition: border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.editor input:hover,
	.editor select:hover {
		border-color: var(--jv-line);
	}
	.hint {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
		max-width: 60ch;
	}
	.days {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-1);
	}
	.save {
		margin-top: var(--jv-space-2);
	}
</style>
