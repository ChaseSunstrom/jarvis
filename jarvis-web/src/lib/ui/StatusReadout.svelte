<!--
@component
The mono readout at the right of the top bar: a few words, each with a dot
that says whether the thing is live. `link · qwen3-8b · stt · tts` on the voice
screen; `2 running · 1 held · 6 done` on WORK. Colour is never the only
signal — the dot's tone and the word agree.

```svelte
<StatusReadout items={[{ label: 'link', tone: 'live' }, { label: '1 held', tone: 'warn' }]} />
```
-->
<script lang="ts">
	export interface ReadoutItem {
		label: string;
		/** `live` lit in the accent, `warn` amber, `off` dim and still, `neutral` plain. */
		tone?: 'live' | 'warn' | 'off' | 'neutral';
		testid?: string;
		/** `status` for the one item a screen reader should follow. */
		role?: 'status';
		busy?: boolean;
		title?: string;
		/** A machine-readable state, rendered as `data-status` for the tests and the CSS. */
		status?: string;
	}
	interface Props {
		items: ReadoutItem[];
		testid?: string;
	}
	let { items, testid = '' }: Props = $props();
</script>

<div class="readout" data-testid={testid || undefined}>
	{#each items as item, i (item.testid ?? `${i}:${item.label}`)}
		<span
			class="item {item.tone ?? 'neutral'}"
			data-testid={item.testid || undefined}
			role={item.role}
			aria-live={item.role === 'status' ? 'polite' : undefined}
			aria-busy={item.busy ? 'true' : undefined}
			data-status={item.status || undefined}
			title={item.title || undefined}
		>
			<i class="dot" aria-hidden="true"></i>
			<span class="word">{item.label}</span>
		</span>
	{/each}
</div>

<style>
	.readout {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--jv-space-4);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-snug);
		color: var(--jv-text-dim);
		white-space: nowrap;
		min-width: 0;
	}
	.item {
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-2);
		min-width: 0;
	}
	.word {
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.dot {
		flex: none;
		width: var(--jv-radius-md);
		height: var(--jv-radius-md);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-text-faint);
		transition: background var(--jv-dur-fast) var(--jv-ease-out), box-shadow var(--jv-dur-fast) var(--jv-ease-out);
	}
	.live .dot {
		background: var(--jv-accent);
		box-shadow: 0 0 var(--jv-radius-md) var(--jv-glow);
		animation: blink var(--jv-dur-blink) var(--jv-ease-in-out) infinite;
	}
	.live .word {
		color: var(--jv-text);
	}
	.warn .dot {
		background: var(--jv-warn);
	}
	.warn .word {
		color: var(--jv-warn);
	}
	.off .dot {
		background: var(--jv-tick);
	}
	.off .word {
		color: var(--jv-text-faint);
	}
	@keyframes blink {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0.35;
		}
	}
	@media (max-width: 640px) {
		/* The dots stay — they are the information — and the words go, except the
		   one a screen reader follows, which is also the one a thumb needs. */
		.item:not([role='status']) .word {
			display: none;
		}
	}
</style>
