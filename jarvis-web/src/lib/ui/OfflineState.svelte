<!--
@component
The link to the backend is down. It says so, says why it will not come back on
its own, and offers the one action worth having.

A page's socket deliberately does not reattach by itself: a reconnected socket
has none of the page's subscriptions, so silently reattaching would leave
somebody reading rows they believe are live. That refusal was right and it left
no way back — a grey "link closed" beside a stale list, and a reload as the only
cure. `onreconnect` re-dials, re-loads and re-subscribes on purpose.

```svelte
<OfflineState onreconnect={connect} busy={redialling} />
```
-->
<script lang="ts">
	interface Props {
		/** What is on screen meanwhile. */
		body?: string;
		/** Re-dial, re-load, re-subscribe. */
		onreconnect?: () => void;
		/** Mid-reconnect: the button says so and stops taking clicks. */
		busy?: boolean;
		testid?: string;
	}
	let {
		body = 'The link to the backend closed, so nothing below is live any more. It will not come back on its own — a reconnected socket has none of this page\'s subscriptions.',
		onreconnect,
		busy = false,
		testid = 'link-dropped'
	}: Props = $props();
</script>

<div class="offline" role="status" data-testid={testid} data-state="offline">
	<span class="dot" aria-hidden="true"></span>
	<div class="text">
		<p class="title">No link to Jarvis</p>
		<p class="body">{body}</p>
	</div>
	{#if onreconnect}
		<button
			class="now"
			type="button"
			disabled={busy}
			onclick={onreconnect}
			data-testid="reconnect"
		>
			{busy ? 'Reconnecting…' : 'Reconnect now'}
		</button>
	{/if}
</div>

<style>
	.offline {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-4);
		margin-bottom: var(--jv-space-3);
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
	.now:hover:not(:disabled) {
		color: var(--jv-text-bright);
		border-color: var(--jv-text-dim);
	}
	.now:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.now:disabled {
		opacity: 0.55;
		cursor: progress;
	}
</style>
