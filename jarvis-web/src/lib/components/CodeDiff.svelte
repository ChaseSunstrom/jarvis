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
	<p class="none" data-testid="code-diff-empty">No changes.</p>
{/if}

<style>
	.head {
		display: flex;
		gap: var(--jv-space-3);
		align-items: baseline;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		margin-bottom: var(--jv-space-2);
	}
	.add {
		color: var(--jv-ok);
	}
	.rem {
		color: var(--jv-danger-text);
	}
	.stat {
		color: var(--jv-text-faint);
	}
	.diff {
		margin: 0;
		max-height: calc(var(--jv-space-7) * 8.66667);
		overflow: auto;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		background: var(--jv-surface-sunken);
		padding: var(--jv-space-3) var(--jv-space-4);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.7;
	}
	.ln {
		display: block;
		white-space: pre;
	}
	.ln.add {
		color: var(--jv-ok);
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
	.none {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
</style>
