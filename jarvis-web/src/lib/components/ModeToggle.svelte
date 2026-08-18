<script lang="ts">
	/**
	 * Orb ⇄ chat, in the flow of whichever surface is showing.
	 *
	 * It used to be one `position: fixed` button in the top-right corner. That
	 * corner is not free: the HUD keeps its status readout and clock there, and
	 * chat mode keeps the speak toggle there — so the switch sat on top of both,
	 * and being fixed it also ate the clicks meant for them.
	 *
	 * A component rendered inside each surface's own header instead. The two
	 * placements cost one extra element each and remove the whole class of
	 * problem: nothing overlaps, nothing needs a z-index, and neither header has
	 * to reserve a magic number of rem for a button it cannot see.
	 */
	let { chat, onToggle }: { chat: boolean; onToggle: () => void } = $props();
</script>

<button
	type="button"
	class="mode"
	data-testid="mode-toggle"
	data-mode={chat ? 'chat' : 'orb'}
	aria-pressed={chat}
	aria-label={chat ? 'Switch to the voice orb' : 'Switch to text chat'}
	title={chat ? 'Voice orb' : 'Text chat'}
	onclick={onToggle}
>
	<span class="glyph" aria-hidden="true">{chat ? '◉' : '▤'}</span>
	<span class="word">{chat ? 'ORB' : 'CHAT'}</span>
</button>

<style>
	.mode {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		padding: 0.24rem 0.7rem;
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-pill);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		/* Inherits the HUD's live state accent where there is one, and falls
		   back to the console's fixed accent everywhere else. */
		color: var(--accent, var(--jv-accent));
		background: transparent;
		cursor: pointer;
		white-space: nowrap;
		transition:
			color var(--jv-dur-fast) var(--jv-ease-out),
			border-color var(--jv-dur-fast) var(--jv-ease-out),
			background var(--jv-dur-fast) var(--jv-ease-out);
	}
	.mode:hover {
		color: var(--jv-text-bright);
		border-color: var(--accent, var(--jv-accent));
		background: var(--jv-wash);
	}
	.mode:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.glyph {
		opacity: 0.85;
	}
	@media (max-width: 480px) {
		.word {
			/* The glyph alone still says which way the switch goes. */
			position: absolute;
			width: 1px;
			height: 1px;
			overflow: hidden;
			clip-path: inset(50%);
		}
	}
</style>
