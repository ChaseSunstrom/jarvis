<script lang="ts">
	/**
	 * What this house has written down.
	 *
	 * A skill is a folder with a `SKILL.md` in it, dropped into the config
	 * directory — so this panel is deliberately read-only apart from a reload
	 * button. There is no "new skill" form and there should not be: a skill is
	 * a document somebody wrote and put on disk, and an editor here would be a
	 * second, worse way to write files on the server.
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
	 */
	import type { Connection } from '$lib/connection';
	import { describeError } from '$lib/connection';
	import { isUnsupported, type Skill, type SkillListing } from '$lib/jarvisClient';
	import { toasts } from '$lib/toast';

	let { conn }: { conn: Connection | null } = $props();

	let skills = $state<Skill[]>([]);
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
	<section class="panel" data-testid="skills-panel">
		<div class="panel-head">
			<span>Skills</span>
			<button class="ghost" onclick={reload} disabled={busy} data-testid="skills-reload">
				{busy ? 'reloading…' : 'reload'}
			</button>
		</div>

		<p class="note">
			Folders with a <code>SKILL.md</code> in them, under the config directory. The assistant
			sees each skill's <b>name and description</b> in every conversation; it reads the body
			only when it decides the skill applies.
		</p>

		{#if err}
			<p class="error" role="alert" data-testid="skills-error">{err}</p>
		{/if}

		{#if skills.length === 0}
			<p class="empty" data-testid="skills-empty">
				No skills loaded. Put a folder with a <code>SKILL.md</code> in it under
				<code>config/skills/</code> and press reload.
			</p>
		{:else}
			<ul class="skills">
				{#each skills as skill (skill.name)}
					<li data-testid="skill-{skill.name}">
						<button class="row" onclick={() => show(skill)} aria-expanded={open === skill.name}>
							<span class="name">{skill.name}</span>
							<span class="desc">{skill.description}</span>
							<span class="size">{skill.body_chars} chars</span>
						</button>
						{#if open === skill.name}
							<div class="body" data-testid="skill-body-{skill.name}">
								{#if skill.allowed_tools.length}
									<p class="tools">
										tools it narrows to: {skill.allowed_tools.join(', ')}
									</p>
								{/if}
								{#if skill.resources.length}
									<p class="tools">
										beside it: {skill.resources.join(', ')} — read, never run
									</p>
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
	</section>
{/if}

<style>
	.note,
	.empty {
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
		margin: var(--jv-space-2) 0 0;
	}
	.skills {
		list-style: none;
		margin: var(--jv-space-3) 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-1);
	}
	.row {
		display: grid;
		grid-template-columns: minmax(6rem, max-content) 1fr max-content;
		gap: var(--jv-space-3);
		align-items: baseline;
		width: 100%;
		text-align: left;
		background: none;
		border: 1px solid transparent;
		border-radius: var(--jv-radius-sm);
		padding: var(--jv-space-2);
		color: inherit;
		cursor: pointer;
		font: inherit;
	}
	.row:hover,
	.row:focus-visible {
		border-color: var(--jv-line);
		background: var(--jv-surface-2);
	}
	.name {
		color: var(--jv-accent);
		font-family: var(--jv-font-mono);
		font-size: var(--jv-fs-sm);
	}
	.desc {
		color: var(--jv-text);
		font-size: var(--jv-fs-sm);
	}
	.size,
	.tools {
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-xs);
	}
	.body {
		padding: 0 var(--jv-space-2) var(--jv-space-2);
	}
	.body pre {
		white-space: pre-wrap;
		max-height: var(--jv-measure-log);
		overflow: auto;
		background: var(--jv-surface-2);
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-sm);
		padding: var(--jv-space-2);
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-xs);
	}
	.errors {
		list-style: none;
		margin: var(--jv-space-3) 0 0;
		padding: 0;
		color: var(--jv-warn);
		font-size: var(--jv-fs-xs);
	}
	.error {
		color: var(--jv-danger);
		font-size: var(--jv-fs-sm);
	}
</style>
