<script lang="ts">
	/**
	 * A unified diff, coloured.
	 *
	 * Scrolls inside itself rather than widening the page: a diff has long
	 * lines by nature and a horizontally scrolling document is a page you
	 * cannot read the rest of.
	 */
	import { countChanges, parseDiff } from '$lib/code';

	let { diff, stat = '' }: { diff: string; stat?: string } = $props();

	const lines = $derived(parseDiff(diff));
	const counts = $derived(countChanges(lines));
</script>

{#if lines.length}
	<div class="head">
		<span class="add">+{counts.added}</span>
		<span class="rem">−{counts.removed}</span>
		{#if stat}<span class="stat">{stat.split('\n').at(-1)}</span>{/if}
	</div>
	<pre class="diff" data-testid="code-diff"><code
			>{#each lines as line, i (i)}<span class="ln {line.kind}">{line.text}
</span>{/each}</code
		></pre>
{:else}
	<p class="muted" data-testid="code-diff-empty">No changes.</p>
{/if}

<style>
	.head {
		display: flex;
		gap: var(--jv-space-2);
		align-items: baseline;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		margin-bottom: var(--jv-space-1);
	}
	.add {
		color: var(--jv-ok, var(--jv-accent));
	}
	.rem {
		color: var(--jv-danger-text);
	}
	.stat {
		color: var(--jv-text-faint);
	}
	.diff {
		margin: 0;
		max-height: 26rem;
		overflow: auto;
		border: 1px solid var(--jv-line-hair);
		background: var(--jv-surface-sunken, transparent);
		padding: var(--jv-space-2);
		font-size: var(--jv-fs-xs);
		line-height: 1.45;
	}
	.ln {
		display: block;
		white-space: pre;
	}
	.ln.add {
		color: var(--jv-ok, var(--jv-accent));
	}
	.ln.remove {
		color: var(--jv-danger-text);
	}
	.ln.hunk {
		color: var(--jv-accent);
	}
	.ln.meta {
		color: var(--jv-text-faint);
	}
	.ln.context {
		color: var(--jv-text-dim);
	}
</style>
