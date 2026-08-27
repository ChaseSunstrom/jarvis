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
	import Markdown from '$lib/components/Markdown.svelte';
	import { looksLikeMarkdown } from '$lib/markdown';
	import { Button, Pill } from '$lib/ui';
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
		<Pill tone={read ? 'neutral' : 'live'}>{kind}</Pill>
		<h3>{title}</h3>
		{#if when}<time>{when}</time>{/if}
	</header>

	{#if body}
		<!-- A card's body is prose Jarvis wrote — a reflection's lesson, a
		     review's finding, a briefing — and reads as it was written (M106). -->
		{#if looksLikeMarkdown(body)}<div class="body"><Markdown text={body} /></div>{:else}<p class="body">{body}</p>{/if}
	{/if}

	<footer>
		{#if link}
			<a class="open" href={link} data-testid={testid ? `${testid}-open` : undefined}>
				OPEN
			</a>
		{/if}
		{#if onread && !read}
			<Button onclick={onread} testid={testid ? `${testid}-read` : undefined}>
				MARK READ
			</Button>
		{/if}
		{#if ondismiss}
			<Button onclick={ondismiss}
				testid={testid ? `${testid}-dismiss` : undefined}>
				DISMISS
			</Button>
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
	/* One moment is one hairline row: the kind as a tag, the title, when, what
	   it said, and quiet actions. Unread carries the inset accent rule. */
	.moment {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
		padding: var(--jv-space-3) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.moment.read {
		box-shadow: none;
	}
	.moment:last-child {
		border-bottom: 0;
	}
	header {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
	}
	h3 {
		margin: 0;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		flex: 1 1 auto;
	}
	time {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
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
	.open {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		text-decoration: none;
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-4);
		transition: color var(--jv-dur-fast) var(--jv-ease-out), border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.open:hover {
		color: var(--jv-text-bright);
		border-color: var(--jv-text-dim);
	}
	.why {
		background: none;
		border: none;
		padding: 0;
		color: var(--jv-text-faint);
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		cursor: pointer;
		text-decoration: underline dotted;
	}
	.why:hover,
	.why:focus-visible {
		color: var(--jv-text);
	}
	.source {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.source code {
		font-family: var(--jv-font-chrome);
		color: var(--jv-text);
	}
</style>
