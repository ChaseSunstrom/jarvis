<!--
@component
A still from a camera — or the reason there is none, which is the more
important half. The frame came through `jarvis/vision/still`, which is the
vision integration's own snapshot path: the camera's consent setting, its rate
limit and its audit row are the same as for a look, so a `never` camera shows
its refusal here and nothing else, and the sentence says so.

No picture is ever cached on the page: a still is what the camera saw when
the dashboard last asked, and the caption says when that was.
```svelte
<CameraStill still={data} camera="Front Door" />
```
-->
<script lang="ts">
	import { clock, stillSentence, type CameraStill } from './widgets';

	interface Props {
		/** null until the first answer comes back. */
		still: CameraStill | null;
		camera?: string;
	}
	let { still, camera = '' }: Props = $props();

	const why = $derived(still ? stillSentence(still) : '');
</script>

{#if !still}
	<p class="why" data-testid="camera-waiting" aria-busy="true">Asking the camera…</p>
{:else if still.status === 'ok' && still.image}
	<figure class="still" data-testid="camera-still">
		<img src={still.image} alt="Still from {still.camera || camera || 'the camera'}" />
		<figcaption>
			<span>{still.camera || camera}</span>
			<span class="when">taken {clock(still.takenAt)}</span>
		</figcaption>
	</figure>
{:else}
	<p class="why" data-testid="camera-why" data-decision={still.decision || still.status}>{why}</p>
{/if}

<style>
	.still {
		display: grid;
		grid-template-rows: minmax(0, 1fr) auto;
		gap: var(--jv-space-2);
		margin: 0;
		min-height: 0;
		height: 100%;
	}
	img {
		display: block;
		width: 100%;
		height: 100%;
		min-height: 0;
		object-fit: cover;
		border-radius: var(--jv-radius-sm);
		border: 1px solid var(--jv-line-hair);
		background: var(--jv-surface-sunken);
	}
	figcaption {
		display: flex;
		justify-content: space-between;
		gap: var(--jv-space-2);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
	}
	.why {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
</style>
