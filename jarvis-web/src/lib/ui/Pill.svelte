<!--
@component
A small status word. `tone` carries the meaning — `ok` done, `warn` held,
`danger` broke, `live` happening now — so the colour is never the only signal:
the word says it too.

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

<span class="pill {tone}" data-testid={testid || undefined}>{@render children()}</span>

<style>
	.pill {
		display: inline-block;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-dim);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-pill);
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
