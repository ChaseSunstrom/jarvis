<!--
@component
The stages of one turn, or one job: a segmented bar whose segments are as long
as the time each stage took, and a list of the stages with their cost. A
stage with no number yet is a dash; the one happening now is lit. Nothing here
is a timer — every width is a measured duration.

```svelte
<StagesBar stages={[{ key: 'stt', label: 'transcribe · whisper', ms: 412 }, { key: 'llm', label: 'first token', ms: null, live: true }]} />
```
-->
<script lang="ts">
	export interface Stage {
		key: string;
		label: string;
		/** Milliseconds, or null while it has not happened. */
		ms: number | null;
		/** Happening now. */
		live?: boolean;
	}
	interface Props {
		stages: Stage[];
		/** Draw only the bar. */
		bare?: boolean;
		testid?: string;
	}
	let { stages, bare = false, testid = '' }: Props = $props();
	const fmt = (ms: number | null): string => (ms === null ? '—' : `${Math.round(ms)} ms`);
</script>

<div class="stages" aria-hidden="true">
	{#each stages as stage (stage.key)}
		<i style:flex={Math.max(1, stage.ms ?? 1)} class:live={stage.live} class:done={stage.ms !== null}></i>
	{/each}
</div>
{#if !bare}
	<dl class="k" data-testid={testid || undefined} aria-label="Stages">
		{#each stages as stage (stage.key)}
			<div><dt>{stage.label}</dt><dd class:live={stage.live}>{fmt(stage.ms)}</dd></div>
		{/each}
	</dl>
{/if}

<style>
	.stages {
		display: flex;
		gap: var(--jv-rule-live);
		height: var(--jv-space-1);
		margin: var(--jv-space-4) var(--jv-space-4) var(--jv-space-2);
	}
	.stages i {
		background: var(--jv-line);
		border-radius: var(--jv-radius-sm);
		transition: flex var(--jv-dur-base) var(--jv-ease-out);
	}
	.stages i.done {
		background: var(--jv-text-dim);
	}
	.stages i.live {
		background: var(--jv-accent);
		box-shadow: 0 0 var(--jv-radius-md) var(--jv-glow);
		animation: jv-blink var(--jv-dur-pulse) var(--jv-ease-in-out) infinite;
	}
	.k {
		margin: 0;
	}
	.k div {
		display: flex;
		justify-content: space-between;
		gap: var(--jv-space-3);
		padding: var(--jv-space-2) var(--jv-space-4);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.k div:last-child {
		border-bottom: 0;
	}
	.k dt,
	.k dd {
		margin: 0;
	}
	.k dd {
		font-family: var(--jv-font-chrome);
		color: var(--jv-text);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.k dd.live {
		color: var(--jv-accent);
	}
	@media (prefers-reduced-motion: reduce) {
		.stages i {
			transition: none;
		}
	}
</style>
