<script lang="ts">
	/**
	 * Chat mode: the same assistant, read instead of heard.
	 *
	 * The orb is the right surface when you are across the room and talking. It
	 * is the wrong one when you want to read an answer, scroll back to what you
	 * asked on Tuesday, or see which tools a turn actually ran. That is what
	 * this is — and it is a *mode*, not a second app: the same socket, the same
	 * pipeline run, the same microphone. Speaking while chat mode is open starts
	 * a turn and lands it in the transcript, because "I switched to typing" is
	 * not "stop listening to me".
	 *
	 * Everything here is presentation. The socket, the pipeline client and the
	 * microphone belong to the page (`routes/+page.svelte`), which owns the one
	 * connection both modes share; this component is handed the transcript and
	 * a set of callbacks. That split is what lets the toggle be instant and
	 * lossless — switching modes does not tear down a run in flight.
	 */
	import { onMount, tick } from 'svelte';
	import ChatMessage from './ChatMessage.svelte';
	import Orb from './Orb.svelte';
	import { relativeTime, type ChatMessage as Message } from '$lib/chat';
	import type { ConversationSummary } from '$lib/jarvisClient';
	import type { PipelineState } from '$lib/pipeline';

	let {
		messages,
		conversations = [],
		conversationId = null,
		historyError = '',
		busy = false,
		turnState = 'idle' as PipelineState,
		muted = false,
		micLabel = '',
		orbLevel = 0,
		speak = false,
		onSend,
		onNew,
		onOpen,
		onDelete,
		onToggleMute,
		onToggleSpeak
	}: {
		messages: Message[];
		conversations?: ConversationSummary[];
		conversationId?: string | null;
		historyError?: string;
		busy?: boolean;
		turnState?: PipelineState;
		muted?: boolean;
		micLabel?: string;
		orbLevel?: number;
		speak?: boolean;
		onSend: (text: string) => void;
		onNew: () => void;
		onOpen: (id: string) => void;
		onDelete: (id: string) => void;
		onToggleMute: () => void;
		onToggleSpeak: () => void;
	} = $props();

	let draft = $state('');
	let scroller = $state<HTMLElement | null>(null);
	let composer = $state<HTMLTextAreaElement | null>(null);
	/** Whether the sidebar is showing. Off-canvas below the breakpoint. */
	let sidebarOpen = $state(false);

	/**
	 * Stick to the bottom only while the reader is already there.
	 *
	 * Scrolling up to re-read something and being yanked back down by the next
	 * token is the one behaviour that makes a streaming transcript unusable.
	 * 80px of slack, so "near the bottom" survives a half-line of new text.
	 */
	let pinned = $state(true);

	function onScroll(): void {
		if (!scroller) return;
		const gap = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
		pinned = gap < 80;
	}

	// Depends on the message array AND on the streaming message's length, so a
	// delta appended to the last message re-runs it. Reading only `messages`
	// would fire on a new message and never again during the stream.
	const tail = $derived(
		messages.length ? `${messages.length}:${messages[messages.length - 1].content.length}` : '0'
	);
	$effect(() => {
		void tail;
		if (!pinned || !scroller) return;
		void tick().then(() => {
			if (scroller) scroller.scrollTop = scroller.scrollHeight;
		});
	});

	function submit(event?: Event): void {
		event?.preventDefault();
		const text = draft.trim();
		if (!text || busy) return;
		draft = '';
		onSend(text);
		// Refocused explicitly: a send that leaves the caret somewhere else makes
		// a conversation a sequence of clicks.
		composer?.focus();
	}

	/** Enter sends; Shift-Enter is a newline, as every chat surface does it. */
	function onComposerKey(event: KeyboardEvent): void {
		if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
			submit(event);
		}
	}

	function openAndClose(id: string): void {
		onOpen(id);
		sidebarOpen = false;
	}

	onMount(() => {
		composer?.focus();
	});
</script>

<section class="chat" data-testid="chat-panel">
	<!-- Off-canvas below the breakpoint, docked above it. `inert` rather than
	     `display: none` when hidden, so a closed drawer's links are not in the
	     tab order on a phone. -->
	<aside
		class="past"
		class:open={sidebarOpen}
		data-testid="chat-history"
		aria-label="Past conversations"
	>
		<div class="past-top">
			<span class="past-title">CONVERSATIONS</span>
			<button
				type="button"
				class="new"
				data-testid="chat-new"
				onclick={() => {
					onNew();
					sidebarOpen = false;
				}}
			>
				+ NEW
			</button>
		</div>

		{#if historyError}
			<p class="past-empty" data-testid="chat-history-error">{historyError}</p>
		{:else if !conversations.length}
			<p class="past-empty">Nothing yet. Ask something.</p>
		{:else}
			<ul>
				{#each conversations as row (row.id)}
					<li class:current={row.id === conversationId}>
						<button
							type="button"
							class="row"
							data-testid="chat-conversation"
							data-id={row.id}
							aria-current={row.id === conversationId ? 'true' : undefined}
							onclick={() => openAndClose(row.id)}
						>
							<span class="row-title">{row.title}</span>
							<span class="row-meta">
								<span class="ago">{relativeTime(row.last_active)}</span>
								<span class="turns">{row.turns}</span>
							</span>
							<span class="row-preview">{row.preview}</span>
						</button>
						<button
							type="button"
							class="forget"
							data-testid="chat-delete"
							aria-label="Forget “{row.title}”"
							title="Forget this conversation"
							onclick={() => onDelete(row.id)}
						>
							×
						</button>
					</li>
				{/each}
			</ul>
		{/if}
	</aside>

	<div class="thread">
		<header class="thread-top">
			<button
				type="button"
				class="drawer"
				data-testid="chat-drawer"
				aria-expanded={sidebarOpen}
				aria-label="Past conversations"
				onclick={() => (sidebarOpen = !sidebarOpen)}
			>
				☰
			</button>
			<div class="mini-orb" aria-hidden="true">
				<Orb level={orbLevel} orbState={turnState} />
			</div>
			<span class="thread-title" data-testid="chat-title">
				{conversations.find((c) => c.id === conversationId)?.title ?? 'New conversation'}
			</span>
			<button
				type="button"
				class="chip"
				class:on={speak}
				data-testid="chat-speak"
				aria-pressed={speak}
				title="Whether typed questions are also answered out loud"
				onclick={onToggleSpeak}
			>
				{speak ? 'SPEAKS' : 'SILENT'}
			</button>
		</header>

		<div
			class="scroll"
			bind:this={scroller}
			onscroll={onScroll}
			data-testid="chat-scroll"
			role="log"
			aria-live="polite"
			aria-label="Conversation"
		>
			{#if !messages.length}
				<div class="empty" data-testid="chat-empty">
					<p class="empty-head">Good evening.</p>
					<p class="empty-sub">
						Type below, or just speak — the microphone stays on in this mode.
					</p>
				</div>
			{:else}
				{#each messages as message (message.id)}
					<ChatMessage {message} />
				{/each}
			{/if}
		</div>

		<form class="composer" onsubmit={submit}>
			<button
				type="button"
				class="mic"
				class:muted
				data-testid="chat-mic"
				aria-pressed={muted}
				aria-label={muted ? 'Unmute the microphone' : 'Mute the microphone'}
				title={micLabel}
				onclick={onToggleMute}
			>
				{muted ? 'MUTED' : 'MIC'}
			</button>
			<textarea
				bind:this={composer}
				bind:value={draft}
				onkeydown={onComposerKey}
				data-testid="chat-input"
				rows="1"
				placeholder="Ask Jarvis…"
				aria-label="Message"
			></textarea>
			<button
				type="submit"
				class="send"
				data-testid="chat-send"
				disabled={!draft.trim() || busy}
			>
				{busy ? '…' : 'SEND'}
			</button>
		</form>
	</div>
</section>

<style>
	.chat {
		position: relative;
		display: grid;
		grid-template-columns: 17rem minmax(0, 1fr);
		height: 100vh;
		height: 100dvh;
		min-height: 0;
		background: var(--jv-bg);
	}

	/* --- past conversations --- */
	.past {
		display: flex;
		flex-direction: column;
		min-height: 0;
		border-right: 1px solid var(--jv-line-soft);
		background: linear-gradient(180deg, var(--jv-bg-raised), var(--jv-bg));
	}
	.past-top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-2);
		padding: var(--jv-space-4) var(--jv-space-3) var(--jv-space-3);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.past-title {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-faint);
	}
	.new {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		color: var(--jv-accent);
		background: var(--jv-wash);
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-pill);
		padding: 0.28rem 0.7rem;
		cursor: pointer;
		white-space: nowrap;
	}
	.new:hover {
		background: var(--jv-wash-strong);
		box-shadow: var(--jv-glow-sm);
	}
	.past ul {
		list-style: none;
		margin: 0;
		padding: var(--jv-space-2);
		overflow-y: auto;
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-height: 0;
	}
	.past li {
		position: relative;
		display: flex;
		align-items: stretch;
	}
	.row {
		flex: 1;
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		grid-template-areas: 'title meta' 'preview preview';
		gap: 0.1rem var(--jv-space-2);
		text-align: left;
		background: transparent;
		border: 1px solid transparent;
		border-radius: var(--jv-radius-sm);
		padding: var(--jv-space-2) var(--jv-space-3);
		cursor: pointer;
		min-width: 0;
	}
	.row:hover {
		background: var(--jv-wash);
		border-color: var(--jv-line-hair);
	}
	li.current .row {
		background: var(--jv-wash);
		border-color: var(--jv-line);
		box-shadow: var(--jv-glow-sm) inset;
	}
	.row-title {
		grid-area: title;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	li.current .row-title {
		color: var(--jv-accent);
	}
	.row-meta {
		grid-area: meta;
		display: flex;
		gap: var(--jv-space-2);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
	}
	.row-preview {
		grid-area: preview;
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		opacity: 0.75;
	}
	.forget {
		flex: 0 0 auto;
		width: 1.9rem;
		background: transparent;
		border: none;
		color: var(--jv-text-faint);
		font-size: var(--jv-fs-md);
		line-height: 1;
		cursor: pointer;
		/* Hidden until the row is hovered or the button itself is focused, so a
		   keyboard user can still reach it and a mouse user is not offered a
		   delete beside every row at all times. */
		opacity: 0;
		transition: opacity var(--jv-dur-fast) var(--jv-ease-out);
	}
	.past li:hover .forget,
	.forget:focus-visible {
		opacity: 1;
	}
	.forget:hover {
		color: var(--jv-danger);
	}
	.past-empty {
		margin: 0;
		padding: var(--jv-space-4) var(--jv-space-3);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}

	/* --- the thread --- */
	.thread {
		display: grid;
		grid-template-rows: auto minmax(0, 1fr) auto;
		min-height: 0;
		min-width: 0;
	}
	.thread-top {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-4);
		/*
		 * Room for the mode switch.
		 *
		 * That button is `position: fixed` in the top-right — it belongs to the
		 * page rather than to either surface, so it has to survive the swap —
		 * and this header's own last control sits in exactly the same place.
		 * Without the reservation the two overlap and the switch, being fixed
		 * and therefore on top, silently eats every click meant for the chip
		 * underneath it.
		 */
		padding-right: 8rem;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.mini-orb {
		width: 2rem;
		height: 2rem;
		flex: 0 0 auto;
	}
	.thread-title {
		flex: 1;
		min-width: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-chrome);
		color: var(--jv-text-dim);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.chip {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		color: var(--jv-text-faint);
		background: transparent;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-pill);
		padding: 0.24rem 0.7rem;
		cursor: pointer;
		white-space: nowrap;
	}
	.chip.on {
		color: var(--jv-gold);
		border-color: color-mix(in srgb, var(--jv-gold) 40%, transparent);
	}
	.drawer {
		display: none;
		background: transparent;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		color: var(--jv-text-dim);
		padding: 0.2rem 0.5rem;
		cursor: pointer;
	}

	.scroll {
		overflow-y: auto;
		padding: var(--jv-space-4);
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-3);
		min-height: 0;
		scroll-behavior: smooth;
	}
	.empty {
		margin: auto;
		text-align: center;
		max-width: 28rem;
	}
	.empty-head {
		margin: 0 0 var(--jv-space-2);
		font-size: var(--jv-fs-display);
		font-weight: 300;
		color: var(--jv-text-bright);
		text-shadow: var(--jv-glow-md);
	}
	.empty-sub {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-faint);
	}

	/* --- composer --- */
	.composer {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: end;
		gap: var(--jv-space-2);
		padding: var(--jv-space-3) var(--jv-space-4) var(--jv-space-4);
		border-top: 1px solid var(--jv-line-hair);
		background: var(--jv-bg-raised);
	}
	textarea {
		resize: none;
		min-height: 2.6rem;
		max-height: 12rem;
		/* `field-sizing` grows the box with its content where it is supported and
		   is ignored where it is not, which is why max-height is set above and
		   the rows attribute is 1: the fallback is a one-line box that scrolls. */
		field-sizing: content;
		padding: 0.6rem 0.8rem;
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		line-height: 1.45;
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
	}
	textarea:focus {
		outline: none;
		border-color: var(--jv-line);
		box-shadow: var(--jv-glow-sm);
	}
	textarea::placeholder {
		color: var(--jv-text-faint);
		opacity: 0.7;
	}
	.send,
	.mic {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		border-radius: var(--jv-radius-pill);
		padding: 0.66rem 1rem;
		cursor: pointer;
		white-space: nowrap;
		transition:
			background var(--jv-dur-fast) var(--jv-ease-out),
			box-shadow var(--jv-dur-fast) var(--jv-ease-out),
			color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.send {
		color: var(--jv-accent-ink);
		background: linear-gradient(180deg, var(--jv-accent-lift), var(--jv-accent));
		border: 1px solid var(--jv-accent);
		font-weight: 600;
	}
	.send:hover:not(:disabled) {
		box-shadow: var(--jv-glow-md);
	}
	.send:disabled {
		opacity: 0.4;
		cursor: default;
	}
	.mic {
		color: var(--jv-accent);
		background: var(--jv-wash);
		border: 1px solid var(--jv-line);
	}
	.mic:hover {
		background: var(--jv-wash-strong);
	}
	/* Muted has to be legible without reading the word: this is the kill
	   switch, and "am I being listened to" is not a question to squint at. */
	.mic.muted {
		color: var(--jv-text-faint);
		background: transparent;
		border-color: color-mix(in srgb, var(--jv-text-faint) 40%, transparent);
	}

	@media (max-width: 800px) {
		.chat {
			grid-template-columns: minmax(0, 1fr);
		}
		.drawer {
			display: block;
		}
		.past {
			position: absolute;
			inset: 0 auto 0 0;
			z-index: 4;
			width: min(18rem, 84vw);
			transform: translateX(-102%);
			transition: transform var(--jv-dur-base) var(--jv-ease-out);
			box-shadow: var(--jv-elev-float);
			background: var(--jv-panel-solid);
		}
		.past.open {
			transform: translateX(0);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.scroll {
			scroll-behavior: auto;
		}
		.past {
			transition: none;
		}
	}
</style>
