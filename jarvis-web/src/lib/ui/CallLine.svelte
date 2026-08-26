<!--
@component
One tool call, as one line: a dot that says how it went, the tool's name in
mono, its arguments, the verdict, and how long it took. Reactor II draws these
under a reply, in the THIS TURN panel and in a task's TOOL CALLS panel — the
same line everywhere, so a tool call looks like a tool call.

```svelte
<CallLine name="light.turn_on" args="kitchen_lamp · 40 %" state="ok" ms={84} />
<CallLine name="run_check" args="npm run check" state="running" />
```
-->
<script lang="ts">
	interface Props {
		name: string;
		args?: string;
		state?: 'running' | 'ok' | 'failed';
		error?: string;
		ms?: number | null;
		/** One line, ellipsised, for a narrow panel. */
		compact?: boolean;
		testid?: string;
	}
	let { name, args = '', state = 'ok', error = '', ms = null, compact = false, testid = '' }: Props = $props();
</script>

<span class="call {state}" class:compact data-testid={testid || undefined} data-state={state}>
	<i aria-hidden="true"></i>
	<b>{name}</b>
	{#if args}<span class="args">{args}</span>{/if}
	{#if state === 'ok'}<em class="ok">ok</em>{:else if state === 'failed'}<em class="bad">{error || 'failed'}</em>{:else}<em class="live">…</em>{/if}
	{#if ms !== null && ms !== undefined}<span class="ms">· {Math.round(ms)} ms</span>{/if}
</span>

<style>
	.call {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-2);
		min-width: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.9;
		color: var(--jv-text-faint);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.call.compact {
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	i {
		flex: none;
		width: var(--jv-space-1);
		height: var(--jv-space-1);
		border-radius: 50%;
		background: var(--jv-ok);
		align-self: center;
	}
	.running i {
		background: var(--jv-accent);
		box-shadow: 0 0 var(--jv-radius-md) var(--jv-glow);
		animation: jv-blink var(--jv-dur-pulse) var(--jv-ease-in-out) infinite;
	}
	.failed i {
		background: var(--jv-danger);
	}
	b {
		font-weight: var(--jv-weight-body);
		color: var(--jv-text-dim);
	}
	.running b {
		color: var(--jv-text-bright);
	}
	.args {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		min-width: 0;
	}
	em {
		font-style: normal;
	}
	.ok {
		color: var(--jv-ok);
	}
	.bad {
		color: var(--jv-danger-text);
	}
	.live {
		color: var(--jv-accent);
	}
	.ms {
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
</style>
