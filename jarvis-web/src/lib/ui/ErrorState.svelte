<!--
@component
What went wrong and what to do about it. The title names the failure in the
user's terms; `detail` carries the machine's words; `onretry` gives them the
one action that might fix it.

```svelte
<ErrorState title="Couldn't load tasks" detail="The backend answered 500." onretry={load} />
```
-->
<script lang="ts">
	interface Props {
		title: string;
		/** The verbatim failure, for somebody who can act on it. */
		detail?: string;
		/** What to try. Shown as a Retry button when given. */
		onretry?: () => void;
		testid?: string;
	}
	let { title, detail = '', onretry, testid = '' }: Props = $props();
</script>

<div class="error" role="alert" data-testid={testid || undefined} data-state="error">
	<p class="title">{title}</p>
	{#if detail}<p class="detail">{detail}</p>{/if}
	{#if onretry}
		<button class="retry" type="button" onclick={onretry} data-testid="retry">Retry</button>
	{/if}
</div>

<style>
	.error {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--jv-space-2);
		padding: var(--jv-space-4);
		border: 1px solid color-mix(in srgb, var(--jv-danger) 35%, transparent);
		border-left: 2px solid var(--jv-danger);
		border-radius: var(--jv-radius-md);
		background: color-mix(in srgb, var(--jv-danger) 7%, var(--jv-panel));
	}
	.title {
		margin: 0;
		font-size: var(--jv-fs-md);
		color: var(--jv-danger-text);
	}
	.detail {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.7;
		color: var(--jv-text-dim);
	}
	.retry {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		background: transparent;
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-4);
		cursor: pointer;
	}
	.retry:hover {
		color: var(--jv-text-bright);
		border-color: var(--jv-text-dim);
	}
	.retry:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
</style>
