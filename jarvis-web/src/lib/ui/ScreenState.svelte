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
	import { isOnline } from '../online';

	export type Status = 'loading' | 'ready' | 'empty' | 'error' | 'offline';

	interface Props {
		status: Status;
		/** Placeholder rows while loading, shaped like the real ones. */
		rows?: number;
		emptyTitle?: string;
		emptyBody?: string;
		errorTitle?: string;
		errorDetail?: string;
		offlineBody?: string | undefined;
		onretry?: () => void;
		onreconnect?: () => void;
		/** Mid-reconnect, for the offline state's button. */
		busy?: boolean;
		testid?: string;
		/** Test ids for the individual states, when a page's suite names them. */
		errorTestid?: string;
		emptyTestid?: string;
		offlineTestid?: string;
		/**
		 * The screen, when the status is `ready`. Omit it on a page that renders
		 * its own regions: this then draws only the state that is not `ready`,
		 * which is how a page with several lists keeps one status region.
		 */
		children?: Snippet;
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
		offlineBody = undefined,
		onretry,
		onreconnect,
		busy = false,
		testid = '',
		errorTestid = '',
		emptyTestid = '',
		offlineTestid = 'link-dropped',
		children,
		emptyAction
	}: Props = $props();

	// Two failures look identical on screen and are not: the relay socket closed,
	// or this machine has no network. Pressing RECONNECT on a laptop whose wifi
	// dropped re-dials into the same wall, so say which one it is.
	const body = $derived(
		isOnline()
			? offlineBody
			: 'This device has no network. Jarvis is probably fine; nothing can reach it from here until the connection is back.'
	);
</script>

<div class="screen" data-screen-state={status} data-testid={testid || undefined}>
	{#if status === 'offline'}
		<!-- The banner AND what was already there: the copy says this is the last
		     state Jarvis sent, so hiding it would make the sentence a lie. -->
		<OfflineState {body} {onreconnect} {busy} testid={offlineTestid} />
		{#if children}{@render children()}{/if}
	{:else if status === 'loading'}
		<SkeletonRows {rows} />
	{:else if status === 'error'}
		<ErrorState title={errorTitle} detail={errorDetail} {onretry} testid={errorTestid} />
	{:else if status === 'empty'}
		<EmptyState title={emptyTitle} body={emptyBody} action={emptyAction} testid={emptyTestid} />
	{:else if children}
		{@render children()}
	{/if}
</div>

<style>
	.screen {
		display: block;
	}
</style>
