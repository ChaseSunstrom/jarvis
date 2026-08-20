<script lang="ts">
	/**
	 * Skills — the procedures Jarvis loads only when they are relevant.
	 *
	 * ## What this page is for
	 *
	 * Reading a skill before it can affect a turn. An installed skill arrives
	 * switched OFF, because it is instructions written by a stranger and there
	 * is no version of "install this but do not do what it says". The body is
	 * shown here, to a person holding a bearer token, and the model does not
	 * get one until somebody has pressed ON.
	 *
	 * The other half is the cost line: the whole argument for skills is that
	 * the prompt stays small, so the page says how many characters the
	 * catalogue is spending. A household that installs thirty skills should be
	 * able to watch that climb.
	 */
	import { onMount } from 'svelte';
	import Reconnect from '$lib/components/Reconnect.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import {
		canForget,
		describeCatalogue,
		describeDisabled,
		describeSource,
		inReadingOrder,
		whyNotReference,
		type SkillDetail,
		type SkillListing,
		type SkillRow
	} from '$lib/skills';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { isUnsupported } from '$lib/jarvisClient';
	import { staggerStyle } from '$lib/motion';
	import { toasts } from '$lib/toast';
	import { DiscardGuard } from '$lib/unsaved';

	let conn = $state<Connection | null>(null);
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');
	let loading = $state(true);
	let redialling = $state(false);
	let listing = $state<SkillListing | null>(null);
	let opened = $state('');
	let detail = $state<SkillDetail | null>(null);
	let busy = $state('');
	let installing = $state(false);
	let reference = $state('');
	let confirming = $state('');

	const rows = $derived(inReadingOrder(listing?.skills ?? []));
	const sources = $derived((listing?.sources ?? []).map((s) => s.project));
	const referenceProblem = $derived(reference ? whyNotReference(reference, sources) : '');
	const costLine = $derived(describeCatalogue(listing));

	const discard = new DiscardGuard((target) =>
		toasts.info(`Remove ${target}?`, 'Press REMOVE again to confirm.')
	);

	async function load(connection: Connection): Promise<void> {
		listing = await connection.client.listSkills();
	}

	async function open(name: string): Promise<void> {
		if (!conn) return;
		if (opened === name) {
			opened = '';
			detail = null;
			return;
		}
		opened = name;
		detail = null;
		try {
			detail = (await conn.client.getSkill(name)).skill;
		} catch (e) {
			err = describeError(e);
		}
	}

	async function toggle(row: SkillRow): Promise<void> {
		if (!conn || busy) return;
		busy = row.name;
		err = '';
		try {
			listing = await conn.client.setSkillEnabled(row.name, !row.enabled);
			toasts.success(
				`${row.name} is ${row.enabled ? 'off' : 'on'}`,
				row.enabled ? 'Jarvis will not see it at all' : 'it is in the prompt from the next turn'
			);
		} catch (e) {
			err = describeError(e);
			toasts.error(`Could not switch ${row.name}`, describeError(e));
		} finally {
			busy = '';
		}
	}

	async function install(): Promise<void> {
		if (!conn || installing || referenceProblem || !reference.trim()) return;
		installing = true;
		err = '';
		try {
			const result = await conn.client.installSkill(reference.trim());
			listing = result;
			const row = result.skill;
			toasts.success(
				`Installed ${row?.name}`,
				row?.enabled ? 'and switched on' : 'switched OFF — read it, then turn it on'
			);
			reference = '';
			if (row?.name) await open(row.name);
		} catch (e) {
			err = describeError(e);
			toasts.error('Could not install it', describeError(e));
		} finally {
			installing = false;
		}
	}

	async function forget(row: SkillRow): Promise<void> {
		if (!conn || busy) return;
		// Always "dirty": there is no undo for a removal, so the first press only
		// ever arms and the second one carries it out.
		if (!discard.allows(row.name, true)) {
			confirming = row.name;
			return;
		}
		confirming = '';
		busy = row.name;
		try {
			listing = await conn.client.forgetSkill(row.name);
			if (opened === row.name) {
				opened = '';
				detail = null;
			}
			toasts.success(`Removed ${row.name}`);
		} catch (e) {
			err = describeError(e);
			toasts.error(`Could not remove ${row.name}`, describeError(e));
		} finally {
			busy = '';
		}
	}

	let disposed = false;
	let dial = 0;

	async function connect(): Promise<void> {
		if (redialling) return;
		redialling = true;
		const mineDial = ++dial;
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
			await load(connection);
		} catch (e) {
			if (isUnsupported(e)) {
				hint = 'this backend has no skills integration — nothing here will fill in';
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
			conn?.close();
			conn = null;
		};
	});
</script>

<svelte:head><title>Jarvis · Skills</title></svelte:head>

<h1>SKILLS</h1>
<p class="lede" data-testid="skills-lede" data-redialling={redialling}>
	{rows.length} skill{rows.length === 1 ? '' : 's'} · link {status}
</p>

<Reconnect {status} busy={redialling} retry={connect} />

{#if err}<p class="err" data-testid="error" role="alert">{err}</p>{/if}
{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}

{#if loading}
	<Skeleton rows={3} />
{:else}
	<section class="panel" data-testid="skills-install">
		<div class="panel-head">
			<span>Install</span>
			<span class="muted" data-testid="skills-cost">{costLine}</span>
		</div>

		<label for="skill-ref">From a permitted repository</label>
		<input
			id="skill-ref"
			type="text"
			placeholder="anthropics/skills/skills/pdf"
			data-testid="skill-reference"
			spellcheck="false"
			autocapitalize="off"
			bind:value={reference}
		/>
		{#if referenceProblem}
			<p class="err" data-testid="skill-reference-problem">{referenceProblem}</p>
		{:else}
			<p class="hint" data-testid="skill-sources">
				Permitted: {sources.join(', ') || 'nothing yet — add one under `skills: sources:`'}.
			</p>
		{/if}

		<div class="row">
			<button
				type="button"
				class="btn"
				data-testid="skill-install"
				disabled={installing || !!referenceProblem || !reference.trim()}
				onclick={install}
			>
				{installing ? 'INSTALLING…' : 'INSTALL'}
			</button>
			<span class="hint">
				A skill is instructions Jarvis follows, so an installed one arrives
				<strong>switched off</strong>. Read it here, then turn it on.
			</span>
		</div>
	</section>

	<section class="panel" data-testid="skills-list">
		<div class="panel-head">
			<span>Skills</span>
			<span class="muted">{rows.filter((r) => r.enabled).length} on</span>
		</div>

		{#each rows as row, i (row.name)}
			<div class="row-wrap jv-stagger" style={staggerStyle(i)}>
				<div class="row" data-testid="skill-{row.name}">
					<button
						type="button"
						class="btn ghost name"
						data-testid="skill-open-{row.name}"
						aria-expanded={opened === row.name}
						onclick={() => open(row.name)}
						disabled={row.source === 'broken'}
					>
						{row.name}
					</button>
					<span class="muted" data-testid="skill-source-{row.name}">{describeSource(row)}</span>
					{#if row.source !== 'broken'}
						<span
							class="state"
							data-testid="skill-state-{row.name}"
							data-enabled={row.enabled ? 'true' : 'false'}
						>
							{row.enabled ? 'on' : 'off'}
						</span>
						<button
							type="button"
							class="btn"
							data-testid="skill-toggle-{row.name}"
							disabled={busy === row.name}
							onclick={() => toggle(row)}
						>
							{busy === row.name ? '…' : row.enabled ? 'TURN OFF' : 'TURN ON'}
						</button>
					{/if}
					{#if canForget(row)}
						<button
							type="button"
							class="btn ghost"
							data-testid="skill-forget-{row.name}"
							disabled={busy === row.name}
							onclick={() => forget(row)}
						>
							{confirming === row.name ? 'REMOVE?' : 'REMOVE'}
						</button>
					{/if}
				</div>

				{#if row.problem}
					<p class="err" data-testid="skill-problem-{row.name}">{row.problem}</p>
				{:else}
					<p class="hint" data-testid="skill-description-{row.name}">{row.description}</p>
					{#if describeDisabled(row)}
						<p class="notice" data-testid="skill-disabled-{row.name}">{describeDisabled(row)}</p>
					{/if}
				{/if}

				{#if opened === row.name}
					<div class="editor" data-testid="skill-detail-{row.name}">
						{#if !detail}
							<Skeleton rows={2} />
						{:else}
							<pre class="body" data-testid="skill-body">{detail.body}</pre>
							<p class="hint">
								{detail.chars} characters. Jarvis reads this only when it opens the skill —
								its prompt carries the one-line description above.
							</p>
						{/if}
					</div>
				{/if}
			</div>
		{/each}
	</section>
{/if}

<style>
	.row .name {
		flex: 1 1 auto;
		text-align: left;
	}
	.state[data-enabled='true'] {
		color: var(--jv-accent);
	}
	.body {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		background: var(--jv-field);
		border-left: 2px solid var(--jv-accent);
		border-radius: var(--jv-radius-sm);
		padding: var(--jv-space-3);
		margin: 0;
		max-height: 28rem;
		overflow: auto;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
</style>
