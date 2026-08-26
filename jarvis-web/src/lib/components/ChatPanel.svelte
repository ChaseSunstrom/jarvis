<script lang="ts">
	/**
	 * Chat mode: the same assistant, read instead of heard.
	 *
	 * The reactor is the right surface when you are across the room and
	 * talking. It is the wrong one when you want to read an answer, scroll back
	 * to what you asked on Tuesday, or see which tools a turn actually ran. That
	 * is what this is — and it is a *mode*, not a second app: the same socket,
	 * the same pipeline run, the same microphone. Speaking while chat mode is
	 * open starts a turn and lands it in the transcript, because "I switched to
	 * typing" is not "stop listening to me".
	 *
	 * Everything here is presentation. The socket, the pipeline client and the
	 * microphone belong to the page (`routes/+page.svelte`), which owns the one
	 * connection both modes share; this component is handed the transcript and
	 * a set of callbacks. That split is what lets the toggle be instant and
	 * lossless — switching modes does not tear down a run in flight.
	 *
	 * On Reactor II it is the voice screen with the transcript expanded: past
	 * conversations in the left panel, the thread in the middle under a small
	 * instrument, the composer in the dock.
	 */
	import { onMount, tick } from 'svelte';
	import ChatMessage from './ChatMessage.svelte';
	import { Reactor } from '$lib/ui';
	import { relativeTime, type ChatMessage as Message } from '$lib/chat';
	import type { ConversationSummary } from '$lib/jarvisClient';

	let {
		messages,
		conversations = [],
		conversationId = null,
		historyError = '',
		busy = false,
		turnState = 'idle',
		muted = false,
		micLabel = '',
		orbLevel = 0,
		speak = false,
		capturing = false,
		onSend,
		onNew,
		onOpen,
		onDelete,
		onVoice,
		onToggleSpeak,
		onToggleMode
	}: {
		messages: Message[];
		conversations?: ConversationSummary[];
		conversationId?: string | null;
		historyError?: string;
		busy?: boolean;
		turnState?: 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';
		muted?: boolean;
		micLabel?: string;
		orbLevel?: number;
		speak?: boolean;
		/** True while this surface is recording a spoken question. */
		capturing?: boolean;
		onSend: (text: string) => void;
		onNew: () => void;
		onOpen: (id: string) => void;
		onDelete: (id: string) => void;
		/** Start (or stop) a spoken turn. Chat mode never listens unasked. */
		onVoice: () => void;
		onToggleSpeak: () => void;
		onToggleMode: () => void;
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
	const SLACK_PX = 80;

	function onScroll(): void {
		if (!scroller) return;
		const gap = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
		pinned = gap < SLACK_PX;
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

	/** The button's tooltip: a real fault if there is one, else what it does. */
	function micError(): string {
		const trouble = micLabel && /BLOCKED|NO MICROPHONE|UNAVAILABLE/.test(micLabel);
		return trouble ? micLabel : 'Press, speak, and pause when you are done';
	}

	function openAndClose(id: string): void {
		onOpen(id);
		sidebarOpen = false;
	}

	const title = $derived(
		conversations.find((c) => c.id === conversationId)?.title ?? 'New conversation'
	);

	onMount(() => {
		composer?.focus();
	});
</script>

<section class="chat" data-testid="chat-panel" data-state={turnState}>
	<!-- Off-canvas below the breakpoint, docked above it. -->
	<aside
		class="past"
		class:open={sidebarOpen}
		data-testid="chat-history"
		aria-label="Past conversations"
	>
		<header class="past-top">
			<span class="past-title">Conversations</span>
			<button
				type="button"
				class="new"
				data-testid="chat-new"
				onclick={() => {
					onNew();
					sidebarOpen = false;
				}}
			>
				+ New
			</button>
		</header>

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
			<div class="mini" aria-hidden="true">
				<Reactor size={120} fluid level={orbLevel} state={turnState} testid="chat-reactor" label="" />
			</div>
			<span class="thread-title" data-testid="chat-title">{title}</span>
			<button
				type="button"
				class="chip"
				class:on={speak}
				data-testid="chat-speak"
				aria-pressed={speak}
				title="Whether replies are also spoken out loud"
				onclick={onToggleSpeak}
			>
				{speak ? 'SPEAKS' : 'SILENT'}
			</button>
			<button
				type="button"
				class="mode"
				data-testid="mode-toggle"
				data-mode="chat"
				aria-pressed={true}
				aria-label="Switch to the voice screen"
				title="Hear the conversation instead of reading it"
				onclick={onToggleMode}
			>
				<span>Voice</span><span class="on">Chat</span>
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
						Ask by typing, or press the microphone to speak. Nothing is heard until you press it.
					</p>
				</div>
			{:else}
				{#each messages as message (message.id)}
					<ChatMessage {message} />
				{/each}
			{/if}
		</div>

		<form class="dock" onsubmit={submit}>
			<!--
			  Press to speak. The reactor's button is a mute switch over an
			  always-on VAD, which is right across a room and wrong at a keyboard;
			  here the same hardware is driven the other way round. Nothing leaves
			  the browser until this is pressed, so it is the privacy boundary
			  too, and chat mode needs no separate mute.
			-->
			<button
				type="button"
				class="mic"
				class:live={capturing}
				class:muted
				data-testid="chat-mic"
				aria-pressed={capturing}
				aria-label={capturing ? 'Stop listening' : 'Speak instead of typing'}
				title={micError()}
				onclick={onVoice}
			>
				<span class="mic-dot" aria-hidden="true"></span>
				{capturing ? 'LISTENING' : 'SPEAK'}
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
		grid-template-columns: calc(var(--jv-space-7) * 6.6667) minmax(0, 1fr);
		gap: var(--jv-space-5);
		height: calc(100vh - var(--jv-space-7) - var(--jv-space-2));
		height: calc(100dvh - var(--jv-space-7) - var(--jv-space-2));
		min-height: 0;
		padding: var(--jv-space-4) var(--jv-space-6) var(--jv-space-6);
		font-family: var(--jv-font-body);
		color: var(--jv-text);
		background: radial-gradient(ellipse 90% 70% at 50% 110%, var(--jv-bg-raised), transparent 70%), var(--jv-bg);
	}
	.past,
	.thread {
		position: relative;
		z-index: 1;
		min-height: 0;
	}

	/* --- past conversations: a panel --- */
	.past {
		display: flex;
		flex-direction: column;
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		overflow: hidden;
		animation: jv-rise var(--jv-dur-enter) var(--jv-ease-out) both;
	}
	.past-top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-2);
		padding: var(--jv-space-3) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.past-title {
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	.new {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		background: transparent;
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-1) var(--jv-space-3);
		cursor: pointer;
		white-space: nowrap;
		transition: color var(--jv-dur-fast) var(--jv-ease-out), border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.new:hover {
		color: var(--jv-text-bright);
		border-color: var(--jv-text-dim);
	}
	.past ul {
		list-style: none;
		margin: 0;
		padding: 0;
		overflow-y: auto;
		display: flex;
		flex-direction: column;
		min-height: 0;
	}
	.past li {
		position: relative;
		display: flex;
		align-items: stretch;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.row {
		flex: 1;
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		grid-template-areas: 'title meta' 'preview preview';
		gap: var(--jv-space-1) var(--jv-space-2);
		text-align: left;
		background: transparent;
		border: 0;
		padding: var(--jv-space-3) var(--jv-space-4);
		cursor: pointer;
		min-width: 0;
		color: inherit;
		font: inherit;
		transition: background var(--jv-dur-fast) var(--jv-ease-out);
	}
	.row:hover {
		background: var(--jv-wash);
	}
	li.current .row {
		background: var(--jv-wash);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.row-title {
		grid-area: title;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	li.current .row-title {
		color: var(--jv-text-bright);
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
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.forget {
		flex: 0 0 auto;
		width: var(--jv-space-6);
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
		color: var(--jv-danger-text);
	}
	.past-empty {
		margin: 0;
		padding: var(--jv-space-4);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}

	/* --- the thread --- */
	.thread {
		display: grid;
		grid-template-rows: auto minmax(0, 1fr) auto;
		gap: var(--jv-space-3);
		min-width: 0;
	}
	.thread-top {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		padding: 0 var(--jv-space-2) var(--jv-space-3);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.mini {
		width: var(--jv-space-6);
		height: var(--jv-space-6);
		flex: 0 0 auto;
	}
	.thread-title {
		flex: 1;
		min-width: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.chip {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-faint);
		background: transparent;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-1) var(--jv-space-3);
		cursor: pointer;
		white-space: nowrap;
		transition: color var(--jv-dur-fast) var(--jv-ease-out), border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.chip:hover {
		color: var(--jv-text);
		border-color: var(--jv-line);
	}
	.chip.on {
		color: var(--jv-gold);
		border-color: color-mix(in srgb, var(--jv-gold) 40%, transparent);
	}
	.mode {
		display: inline-flex;
		gap: var(--jv-space-4);
		background: transparent;
		border: 0;
		padding: 0;
		cursor: pointer;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-faint);
		white-space: nowrap;
	}
	.mode span {
		padding-bottom: var(--jv-space-1);
		border-bottom: 1px solid transparent;
		transition: color var(--jv-dur-fast) var(--jv-ease-out), border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.mode span.on {
		color: var(--jv-text-bright);
		border-bottom-color: var(--jv-accent);
	}
	.mode:hover span:not(.on) {
		color: var(--jv-text);
	}
	.drawer {
		display: none;
		background: transparent;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		color: var(--jv-text-dim);
		padding: var(--jv-space-1) var(--jv-space-2);
		cursor: pointer;
	}

	.scroll {
		overflow-y: auto;
		padding: var(--jv-space-2) var(--jv-space-2);
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-3);
		min-height: 0;
		scroll-behavior: smooth;
	}
	.empty {
		margin: auto;
		text-align: center;
		max-width: 44ch;
	}
	.empty-head {
		margin: 0 0 var(--jv-space-2);
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-display);
		color: var(--jv-text-bright);
	}
	.empty-sub {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}

	/* --- the dock: the composer --- */
	.dock {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: end;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-3);
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
	}
	textarea {
		resize: none;
		min-height: var(--jv-space-6);
		max-height: calc(var(--jv-space-7) * 4);
		/* `field-sizing` grows the box with its content where it is supported and
		   is ignored where it is not, which is why max-height is set above and
		   the rows attribute is 1: the fallback is a one-line box that scrolls. */
		field-sizing: content;
		padding: var(--jv-space-2) var(--jv-space-2);
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-md);
		line-height: 1.45;
		color: var(--jv-text-bright);
		background: transparent;
		border: 0;
		border-bottom: 1px solid transparent;
		transition: border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	textarea:focus {
		outline: none;
		border-bottom-color: var(--jv-line);
	}
	textarea::placeholder {
		color: var(--jv-text-faint);
	}
	.send,
	.mic {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-4);
		cursor: pointer;
		white-space: nowrap;
		transition: background var(--jv-dur-fast) var(--jv-ease-out),
			border-color var(--jv-dur-fast) var(--jv-ease-out),
			color var(--jv-dur-fast) var(--jv-ease-out);
	}
	/* The one filled control on the screen. */
	.send {
		color: var(--jv-accent-ink);
		background: var(--jv-accent);
		border: 1px solid var(--jv-accent);
	}
	.send:hover:not(:disabled) {
		background: var(--jv-accent-lift);
		border-color: var(--jv-accent-lift);
	}
	.send:disabled {
		opacity: 0.45;
		cursor: default;
	}
	.mic {
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-2);
		color: var(--jv-text-dim);
		background: transparent;
		border: 1px solid var(--jv-line);
	}
	.mic:hover {
		color: var(--jv-text-bright);
		border-color: var(--jv-text-dim);
	}
	.mic-dot {
		width: var(--jv-radius-md);
		height: var(--jv-radius-md);
		border-radius: 50%;
		background: currentColor;
		opacity: 0.55;
	}
	/*
	 * Recording has to be unmistakable without reading the word. This is the
	 * only state in which audio leaves the browser, so it is the one thing on
	 * this surface that gets the danger colour and a pulse.
	 */
	.mic.live {
		color: var(--jv-danger-text);
		border-color: color-mix(in srgb, var(--jv-danger) 55%, transparent);
		background: color-mix(in srgb, var(--jv-danger) 10%, transparent);
	}
	.mic.live .mic-dot {
		background: var(--jv-danger);
		opacity: 1;
		animation: jv-blink var(--jv-dur-enter) var(--jv-ease-in-out) infinite;
	}

	@media (max-width: 800px) {
		.chat {
			grid-template-columns: minmax(0, 1fr);
			padding: var(--jv-space-3);
		}
		.drawer {
			display: block;
		}
		.past {
			position: absolute;
			inset: 0 auto 0 0;
			z-index: 4;
			width: min(calc(var(--jv-space-7) * 6), 84vw);
			transform: translateX(-102%);
			transition: transform var(--jv-dur-base) var(--jv-ease-out);
			box-shadow: var(--jv-elev-float);
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
