<script lang="ts">
	/**
	 * One thing Jarvis said without being asked, drawn as a moment.
	 *
	 * The audit's words for what was missing: deliveries were "companion pushes
	 * and toasts, not designed UI moments; no notification record to retrieve".
	 * A toast is the wrong shape for this — it is gone in four seconds, and the
	 * whole point of a proactive message is that it arrives when you are not
	 * looking.
	 *
	 * So a moment is: what happened, in one line you can read across a room;
	 * what it said, in the assistant's own words; where to go to see the thing
	 * itself; and — the part that answers "why am I seeing this" — the event
	 * that produced it. Every one of those is a field on the record, not a
	 * reconstruction.
	 */
	import type { Snippet } from 'svelte';

	interface Props {
		kind: string;
		title: string;
		body?: string;
		at?: number;
		read?: boolean;
		/** The bus event that produced it: the honest answer to "why this?". */
		source?: string;
		link?: string;
		testid?: string;
		ondismiss?: () => void;
		onread?: () => void;
		actions?: Snippet;
	}

	let {
		kind,
		title,
		body = '',
		at = 0,
		read = false,
		source = '',
		link = '',
		testid = '',
		ondismiss,
		onread,
		actions
	}: Props = $props();

	let why = $state(false);

	const when = $derived(
		at
			? new Date(at * 1000).toLocaleString(undefined, {
					hour: '2-digit',
					minute: '2-digit',
					day: 'numeric',
					month: 'short'
				})
			: ''
	);
</script>

<article class="moment" class:read data-kind={kind} data-testid={testid || undefined}>
	<header>
		<span class="kind">{kind}</span>
		<h3>{title}</h3>
		{#if when}<time>{when}</time>{/if}
	</header>

	{#if body}<p class="body">{body}</p>{/if}

	<footer>
		{#if link}
			<a class="btn ghost" href={link} data-testid={testid ? `${testid}-open` : undefined}>
				OPEN
			</a>
		{/if}
		{#if onread && !read}
			<button class="btn ghost" onclick={onread} data-testid={testid ? `${testid}-read` : undefined}>
				MARK READ
			</button>
		{/if}
		{#if ondismiss}
			<button
				class="btn ghost"
				onclick={ondismiss}
				data-testid={testid ? `${testid}-dismiss` : undefined}
			>
				DISMISS
			</button>
		{/if}
		{#if source}
			<button
				class="why"
				onclick={() => (why = !why)}
				aria-expanded={why}
				data-testid={testid ? `${testid}-why` : undefined}
			>
				WHY AM I SEEING THIS?
			</button>
		{/if}
		{@render actions?.()}
	</footer>

	{#if why && source}
		<p class="source" data-testid={testid ? `${testid}-source` : undefined}>
			Jarvis sent this because <code>{source}</code> happened.
		</p>
	{/if}
</article>

<style>
	.moment {
		border: 1px solid var(--jv-line);
		border-left: 2px solid var(--jv-accent);
		border-radius: var(--jv-radius-md);
		background: var(--jv-surface-1);
		padding: var(--jv-space-3);
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
	}
	.moment.read {
		border-left-color: var(--jv-line);
	}
	header {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
	}
	h3 {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		flex: 1 1 auto;
	}
	.kind,
	time,
	.source {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
		text-transform: uppercase;
		letter-spacing: var(--jv-track-chrome);
	}
	.body {
		margin: 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	footer {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
		align-items: center;
	}
	.why {
		background: none;
		border: none;
		padding: 0;
		color: var(--jv-text-faint);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		cursor: pointer;
		text-decoration: underline dotted;
	}
	.why:hover,
	.why:focus-visible {
		color: var(--jv-accent);
	}
	.source {
		margin: 0;
		text-transform: none;
		letter-spacing: normal;
	}
	.source code {
		color: var(--jv-accent);
	}
</style>
