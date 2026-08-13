<script lang="ts">
	/**
	 * The way back from a dropped socket.
	 *
	 * A management page's connection deliberately does not reattach by itself —
	 * `consoleLink.ts` says why: a page that lost its socket also lost its
	 * subscriptions, and silently reconnecting would leave somebody reading rows
	 * they believe are live. That reasoning is sound and it left a hole. There
	 * was no way back at all: the header said OFFLINE, the page said "link
	 * closed" in small grey text beside a stale list, and the only cure anybody
	 * could find was to reload the tab.
	 *
	 * So the page keeps its refusal to reconnect on its own, and offers a button
	 * that does it on purpose. Pressing it re-dials, re-runs the page's own load
	 * and re-subscribes — the same three steps `onMount` does, which is exactly
	 * what makes the rows trustworthy again.
	 *
	 * Shown only when the link is actually down. `connecting` is not down, it is
	 * the first second of every page load, and a RECONNECT button that flashes
	 * up during a normal load teaches people to ignore it.
	 */
	let {
		status,
		busy = false,
		retry
	}: { status: string; busy?: boolean; retry: () => void } = $props();

	let down = $derived(status === 'closed' || status === 'error');
</script>

{#if down}
	<div class="notice dropped" data-testid="link-dropped" role="status">
		<span>
			The link to the backend closed, so nothing below is live any more. It will not come back
			on its own — a reconnected socket has none of this page's subscriptions.
		</span>
		<button
			type="button"
			class="btn"
			data-testid="reconnect"
			disabled={busy}
			onclick={retry}
		>
			{busy ? 'RECONNECTING…' : 'RECONNECT'}
		</button>
	</div>
{/if}

<style>
	/* `.notice` already colours it; this only puts the button on the end of the
	   sentence rather than under it. */
	.dropped {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
	}
	.dropped span {
		flex: 1 1 18rem;
		min-width: 0;
	}
</style>
