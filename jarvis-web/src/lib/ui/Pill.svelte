<!--
@component
A small status word, as a hairline tag. `tone` carries the meaning — `ok`
done, `warn` held, `danger` broke, `live` happening now — so the colour is
never the only signal: the word says it too. Reactor II has no pills: this is
a square-cornered tag on a hairline, and it keeps its old name so nothing that
used it has to change.

```svelte
<Pill tone="warn">held</Pill>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	interface Props {
		tone?: 'neutral' | 'live' | 'ok' | 'warn' | 'danger';
		testid?: string;
		children: Snippet;
	}
	let { tone = 'neutral', testid = '', children }: Props = $props();
</script>

<span class="tag {tone}" data-testid={testid || undefined}>{@render children()}</span>

<style>
	.tag {
		display: inline-block;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		line-height: 1.6;
		color: var(--jv-text-dim);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		padding: 0 var(--jv-space-2);
		white-space: nowrap;
	}
	.live {
		color: var(--jv-accent);
		border-color: color-mix(in srgb, var(--jv-accent) 40%, transparent);
	}
	.ok {
		color: var(--jv-ok);
		border-color: color-mix(in srgb, var(--jv-ok) 40%, transparent);
	}
	.warn {
		color: var(--jv-warn);
		border-color: color-mix(in srgb, var(--jv-warn) 40%, transparent);
	}
	.danger {
		color: var(--jv-danger-text);
		border-color: color-mix(in srgb, var(--jv-danger) 40%, transparent);
	}
</style>
