<!--
@component
The four states every screen owes its user, in one place: **loading**,
**empty**, **error**, **offline** — and `ready`, which renders the screen.

A screen cannot forget a state by not writing it: it declares one `status` and
this decides what is on the page. `scripts/verify/web_states_check.py` requires
every routed page to use it, and `e2e/states.spec.ts` drives all four.

```svelte
<ScreenState status={status} emptyTitle="Nothing running"
	emptyBody="Ask Jarvis for something." errorTitle="Couldn't load tasks"
	errorDetail={error} onretry={load} onreconnect={dial}>
	{#snippet children()}<TaskList {tasks} />{/snippet}
</ScreenState>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import SkeletonRows from './SkeletonRows.svelte';
	import EmptyState from './EmptyState.svelte';
	import ErrorState from './ErrorState.svelte';
	import OfflineState from './OfflineState.svelte';

	export type Status = 'loading' | 'ready' | 'empty' | 'error' | 'offline';

	interface Props {
		status: Status;
		/** Placeholder rows while loading, shaped like the real ones. */
		rows?: number;
		emptyTitle?: string;
		emptyBody?: string;
		errorTitle?: string;
		errorDetail?: string;
		offlineBody?: string;
		onretry?: () => void;
		onreconnect?: () => void;
		testid?: string;
		children: Snippet;
		/** A control that would fill an empty screen. */
		emptyAction?: Snippet;
	}
	let {
		status,
		rows = 4,
		emptyTitle = 'Nothing here yet',
		emptyBody = '',
		errorTitle = "Couldn't load this",
		errorDetail = '',
		offlineBody = 'Reconnecting. What you see is the last state Jarvis sent.',
		onretry,
		onreconnect,
		testid = '',
		children,
		emptyAction
	}: Props = $props();
</script>

<div class="screen" data-screen-state={status} data-testid={testid || undefined}>
	{#if status === 'loading'}
		<SkeletonRows {rows} />
	{:else if status === 'offline'}
		<OfflineState body={offlineBody} {onreconnect} />
	{:else if status === 'error'}
		<ErrorState title={errorTitle} detail={errorDetail} {onretry} />
	{:else if status === 'empty'}
		<EmptyState title={emptyTitle} body={emptyBody} action={emptyAction} />
	{:else}
		{@render children()}
	{/if}
</div>

<style>
	.screen {
		display: block;
	}
</style>
