<script lang="ts">
	/**
	 * Prose Jarvis wrote, drawn as it was written (M106). The HTML comes from
	 * `renderMarkdown`, which escapes the text before it draws a conservative
	 * subset — see `$lib/markdown` for what it will and will not render.
	 */
	import { renderMarkdown } from '$lib/markdown';

	let { text = '', testid = undefined as string | undefined }: { text?: string; testid?: string } = $props();
	const html = $derived(renderMarkdown(text));
</script>

<div class="md" data-testid={testid}>{@html html}</div>

<style>
	.md :global(p) {
		margin: 0 0 var(--jv-space-2);
	}
	.md :global(p:last-child) {
		margin-bottom: 0;
	}
	.md :global(h1),
	.md :global(h2),
	.md :global(h3),
	.md :global(h4),
	.md :global(h5),
	.md :global(h6) {
		margin: var(--jv-space-3) 0 var(--jv-space-1);
		font-family: var(--jv-font-display, inherit);
		font-weight: 600;
		letter-spacing: var(--jv-track-snug);
		line-height: 1.2;
	}
	.md :global(h1) {
		font-size: var(--jv-fs-lg);
	}
	.md :global(h2) {
		font-size: var(--jv-fs-md);
	}
	.md :global(h3) {
		font-size: var(--jv-fs-sm);
	}
	.md :global(ul),
	.md :global(ol) {
		margin: 0 0 var(--jv-space-2);
		padding-left: var(--jv-space-5);
	}
	.md :global(li) {
		margin: var(--jv-space-1) 0;
	}
	.md :global(code) {
		font-family: var(--jv-font-mono, ui-monospace, monospace);
		font-size: var(--jv-fs-xs);
		padding: 0 var(--jv-space-1);
		border-radius: var(--jv-radius-sm);
		background: color-mix(in srgb, currentColor 10%, transparent);
	}
	.md :global(pre) {
		margin: 0 0 var(--jv-space-2);
		padding: var(--jv-space-2) var(--jv-space-3);
		overflow-x: auto;
		border-radius: var(--jv-radius-sm);
		background: color-mix(in srgb, currentColor 8%, transparent);
	}
	.md :global(pre code) {
		padding: 0;
		background: none;
	}
	.md :global(blockquote) {
		margin: 0 0 var(--jv-space-2);
		padding-left: var(--jv-space-3);
		border-left: 2px solid color-mix(in srgb, currentColor 35%, transparent);
		opacity: 0.9;
	}
	.md :global(hr) {
		border: 0;
		border-top: 1px solid color-mix(in srgb, currentColor 25%, transparent);
		margin: var(--jv-space-3) 0;
	}
	.md :global(a) {
		color: inherit;
		text-decoration: underline;
		text-underline-offset: 0.15em;
	}
</style>
