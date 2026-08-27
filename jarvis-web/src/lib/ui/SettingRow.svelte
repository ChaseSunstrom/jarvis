<!--
@component
One settings row, the same on every tab (M107): what it is on the left, the
control in the middle, the actions on the right — on one grid, so the value
column starts at the same edge in every panel and every control fills its
cell. Before this each section drew its own grid, and the review pictures
showed the value column at 617 px in one panel, 582 px in the next and 611 px
in the third, with four widths of control in one panel.

```svelte
<SettingRow label="Wake word" why="What you say to get its attention." testid="plain-voice.wake_word">
  <Select … />
  {#snippet acts()}<Button>SAVE</Button>{/snippet}
</SettingRow>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		/** The setting's name, in plain words. */
		label?: string;
		/** One line on why anybody would change it. */
		why?: string;
		/** Richer than `label`/`why`: the whole left cell. */
		what?: Snippet;
		/** The control — an input, a select, a value. */
		children?: Snippet;
		/** SAVE, RESET, REMOVE … right-aligned. */
		acts?: Snippet;
		/** A line under the row, full width: an error, a note, a package notice. */
		note?: Snippet;
		/**
		 * Whether the note has anything in it. A snippet declared inside an
		 * `{#if}` never reaches the component (Svelte passes only direct
		 * children as snippet props), so the caller declares it always and
		 * says here whether to draw the line — the package notice under a
		 * locked row went missing that way on 27 Aug 2026.
		 */
		noted?: boolean;
		testid?: string;
		/** The row is the one being edited or saved. */
		live?: boolean;
	}
	let { label = '', why = '', what, children, acts, note, noted = true, testid = undefined, live = false }: Props = $props();
</script>

<div class="setting" class:live data-testid={testid} data-jv-row>
	<div class="what">
		{#if what}{@render what()}{:else}
			<b>{label}</b>
			{#if why}<span class="why">{why}</span>{/if}
		{/if}
	</div>
	<div class="control" data-jv-value>{@render children?.()}</div>
	<div class="acts">{@render acts?.()}</div>
	{#if note && noted}<div class="note">{@render note()}</div>{/if}
</div>

<style>
	/* One setting: what it is, the control, the actions — on a hairline.
	   The three columns are the same in every section that uses this. */
	.setting {
		display: grid;
		/* A FIXED label column, not a share: with `1fr` the value column's edge
		   followed each container's width, and the EVERYTHING fold sat 18 px
		   left of the panel above it. */
		grid-template-columns: minmax(12rem, 18rem) minmax(10rem, 1fr) 10rem;
		/* The acts column is FIXED too: with `auto` a row carrying SAVE + TEST took
		   67 px more than a row with nothing there, and the inputs above and below it
		   ended at three different right edges. A fixed slot costs an empty row 10rem
		   of right margin, which is the price of one ruler for the whole tab. */
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
		padding: var(--jv-space-3) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.setting:last-child {
		border-bottom: 0;
	}
	.what {
		display: grid;
		gap: var(--jv-space-1);
		min-width: 0;
	}
	.what :global(b) {
		font-weight: var(--jv-weight-label);
		color: var(--jv-text-bright);
	}
	.why {
		font-size: var(--jv-fs-xs);
		line-height: 1.5;
		color: var(--jv-text-dim);
		max-width: 44ch;
	}
	.control {
		min-width: 0;
	}
	/* Every control fills its cell: a native select, an input, a field —
	   the "four widths in one panel" of the review pictures were each
	   control at its own natural width. */
	.control :global(select),
	.control :global(input:not([type='checkbox']):not([type='radio'])),
	.control :global(textarea) {
		width: 100%;
		box-sizing: border-box;
	}
	.acts {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
	}
	.note {
		grid-column: 1 / -1;
		min-width: 0;
	}
	.note :global(p) {
		margin: 0;
		font-size: var(--jv-fs-xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
		max-width: 80ch;
	}
	@media (max-width: 720px) {
		.setting {
			grid-template-columns: minmax(0, 1fr);
		}
		.acts {
			justify-content: flex-start;
		}
	}
</style>
