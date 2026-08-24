<!--
@component
The link to Jarvis is down. Says so, says what is on screen is the last known
state, and offers the one action worth having.

```svelte
<OfflineState onreconnect={dial} />
```
-->
<script lang="ts">
	interface Props {
		/** What the screen is showing meanwhile. */
		body?: string;
		onreconnect?: () => void;
		testid?: string;
	}
	let {
		body = 'Reconnecting. What you see is the last state Jarvis sent.',
		onreconnect,
		testid = ''
	}: Props = $props();
</script>

<div class="offline" role="status" data-testid={testid || undefined} data-state="offline">
	<span class="dot" aria-hidden="true"></span>
	<div class="text">
		<p class="title">No link to Jarvis</p>
		<p class="body">{body}</p>
	</div>
	{#if onreconnect}
		<button class="now" type="button" onclick={onreconnect} data-testid="reconnect">
			Reconnect now
		</button>
	{/if}
</div>

<style>
	.offline {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-4);
		border: 1px solid var(--jv-line);
		border-left: 2px solid var(--jv-warn);
		border-radius: var(--jv-radius-md);
		background: var(--jv-panel);
	}
	.dot {
		width: var(--jv-space-2);
		height: var(--jv-space-2);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-warn);
		flex: none;
	}
	.text {
		flex: 1;
	}
	.title {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
	}
	.body {
		margin: 0;
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
	}
	.now {
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
		white-space: nowrap;
	}
	.now:hover {
		color: var(--jv-text-bright);
		border-color: var(--jv-text-dim);
	}
	.now:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
</style>
