<script lang="ts">
	/**
	 * What this house has written down.
	 *
	 * A skill is a folder with a `SKILL.md` in it, dropped into the config
	 * directory — so this panel is deliberately read-only apart from a reload
	 * button. There is no "new skill" form here and there should not be: the
	 * guided scaffold lives with the extensions list, and an editor here would
	 * be a second, worse way to write files on the server.
	 *
	 * Two things it is really for:
	 *
	 * **Saying what the model can see.** Only a skill's name and description
	 * reach the system prompt; the body arrives when the model calls
	 * `use_skill`. The panel shows exactly that split, so nobody is surprised
	 * that a two-thousand-word skill is not "loaded into" the assistant.
	 *
	 * **Showing the ones that failed.** A mistyped frontmatter makes a skill
	 * silently absent, which is the least diagnosable failure a folder-based
	 * feature has. Errors are listed with the path and the reason.
	 *
	 * It draws no panel of its own: the tools page puts it behind a disclosure
	 * whose header carries the count this reports through `count`.
	 */
	import type { Connection } from '$lib/connection';
	import { describeError } from '$lib/connection';
	import { isUnsupported, type Skill, type SkillListing } from '$lib/jarvisClient';
	import { toasts } from '$lib/toast';
	import { Button } from '$lib/ui';

	// `epoch` ticks when the catalogue above installs something (M65): a skill
	// that just landed has to appear here without a reload.
	let { conn, count = $bindable(0), query = '', matches = $bindable(0), epoch = 0 }: { conn: Connection | null; count?: number; query?: string; matches?: number; epoch?: number } = $props();
	/** The tools page's one search (M55): a row matches when any of its words do. */
	function matchesQuery(row: object, q: string): boolean {
		const needle = q.trim().toLowerCase();
		if (!needle) return true;
		return Object.values(row as Record<string, unknown>)
			.filter((v): v is string => typeof v === 'string')
			.join(' ')
			.toLowerCase()
			.includes(needle);
	}


	let skills = $state<Skill[]>([]);
	const shown = $derived(skills.filter((s) => matchesQuery(s, query)));
	$effect(() => {
		matches = shown.length;
	});
	let errors = $state<{ path: string; error: string }[]>([]);
	let supported = $state(true);
	let loaded = $state(false);
	let busy = $state(false);
	let err = $state('');
	let open = $state('');
	let bodies = $state<Record<string, string>>({});

	function take(listing: SkillListing | null | undefined): void {
		skills = listing?.skills ?? [];
		errors = listing?.errors ?? [];
		count = skills.length;
	}

	async function refresh(connection: Connection): Promise<void> {
		try {
			take(await connection.client.listSkills());
			supported = true;
		} catch (e) {
			// An older jarvis-core has no skills integration: draw nothing
			// rather than a fault.
			if (isUnsupported(e)) supported = false;
			else err = describeError(e);
		} finally {
			loaded = true;
		}
	}

	$effect(() => {
		const connection = conn;
		void epoch;
		if (!connection) return;
		void refresh(connection);
	});

	async function reload(): Promise<void> {
		if (!conn || busy) return;
		busy = true;
		err = '';
		try {
			const result = await conn.client.reloadSkills();
			await refresh(conn);
			toasts.success(
				`${result.loaded} skill${result.loaded === 1 ? '' : 's'} loaded`,
				result.errors.length ? `${result.errors.length} could not be read` : undefined
			);
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = false;
		}
	}

	async function show(skill: Skill): Promise<void> {
		if (open === skill.name) {
			open = '';
			return;
		}
		open = skill.name;
		if (bodies[skill.name] !== undefined || !conn) return;
		try {
			const answer = await conn.client.getSkill(skill.name);
			bodies = { ...bodies, [skill.name]: answer.skill.body ?? '' };
		} catch (e) {
			bodies = { ...bodies, [skill.name]: `could not be read: ${describeError(e)}` };
		}
	}
</script>

{#if supported && loaded}
	<div class="skills-panel" data-testid="skills-panel">
		<div class="head">
			<p class="note">
				Folders with a <code>SKILL.md</code> in them, under the config directory. The assistant
				sees each skill's <b>name and description</b> in every conversation; it reads the body
				only when it decides the skill applies.
			</p>
			<Button testid="skills-reload" disabled={busy} title={busy ? 'Reloading' : 'Read the skills folder again'} onclick={reload}>
				{busy ? 'RELOADING…' : 'RELOAD'}
			</Button>
		</div>

		{#if err}
			<p class="bad" role="alert" data-testid="skills-error">{err}</p>
		{/if}

		{#if skills.length === 0}
			<p class="note" data-testid="skills-empty">
				No skills loaded. Put a folder with a <code>SKILL.md</code> in it under
				<code>config/skills/</code> and press reload.
			</p>
		{:else}
			<ul class="skills">
				{#each shown as skill (skill.name)}
					<li data-testid="skill-{skill.name}" data-jv-row>
						<button class="skill" onclick={() => show(skill)} aria-expanded={open === skill.name}>
							<span class="name">{skill.name}</span>
							<span class="desc">{skill.description}</span>
							<span class="size">{skill.body_chars} chars</span>
						</button>
						{#if open === skill.name}
							<div class="body" data-testid="skill-body-{skill.name}">
								{#if skill.allowed_tools.length}
									<p class="tools">tools it narrows to: {skill.allowed_tools.join(', ')}</p>
								{/if}
								{#if skill.resources.length}
									<p class="tools">beside it: {skill.resources.join(', ')} — read, never run</p>
								{/if}
								<pre>{bodies[skill.name] ?? 'reading…'}</pre>
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}

		{#if errors.length}
			<ul class="errors" data-testid="skill-errors">
				{#each errors as problem (problem.path)}
					<li><code>{problem.path}</code> — {problem.error}</li>
				{/each}
			</ul>
		{/if}
	</div>
{/if}

<style>
	.head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: var(--jv-space-4);
		flex-wrap: wrap;
		margin-bottom: var(--jv-space-2);
	}
	.note {
		margin: 0;
		flex: 1 1 24rem;
		max-width: 70ch;
		font-size: var(--jv-fs-xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
	}
	.note b {
		font-weight: var(--jv-weight-label);
		color: var(--jv-text);
	}
	code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text);
	}
	.bad {
		margin: 0;
		padding: var(--jv-space-2) 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-danger-text);
	}
	.skills {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.skills > li {
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.skills > li:last-child {
		border-bottom: 0;
	}
	/* The whole row is the disclosure; it has no button chrome. */
	.skill {
		display: grid;
		grid-template-columns: minmax(6rem, max-content) 1fr max-content;
		gap: var(--jv-space-3);
		align-items: baseline;
		width: 100%;
		text-align: left;
		background: none;
		border: 0;
		padding: var(--jv-space-3) var(--jv-space-2);
		color: inherit;
		cursor: pointer;
		font: inherit;
		transition: background var(--jv-dur-fast) var(--jv-ease-out);
	}
	.skill:hover,
	.skill[aria-expanded='true'] {
		background: var(--jv-wash);
	}
	.skill:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: calc(-1 * var(--jv-focus-offset));
	}
	/* A skill's name is an identifier the model says: data, so mono. */
	.name {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-bright);
	}
	.desc {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		min-width: 0;
	}
	.size,
	.tools {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.tools {
		margin: 0 0 var(--jv-space-2);
	}
	.body {
		padding: 0 var(--jv-space-2) var(--jv-space-3);
	}
	.body pre {
		margin: 0;
		white-space: pre-wrap;
		max-height: var(--jv-measure-log);
		overflow: auto;
		background: var(--jv-surface-sunken);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		padding: var(--jv-space-3);
		color: var(--jv-text-dim);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.6;
	}
	.errors {
		list-style: none;
		margin: var(--jv-space-3) 0 0;
		padding: 0;
		color: var(--jv-warn);
		font-size: var(--jv-fs-xs);
	}
	.errors code {
		color: var(--jv-warn);
	}
	@media (max-width: 640px) {
		.skill {
			grid-template-columns: minmax(0, 1fr) max-content;
		}
		.desc {
			grid-column: 1 / -1;
		}
	}
</style>
