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
	import ModeToggle from './ModeToggle.svelte';
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
		capturing = false,
		accent = '',
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
		turnState?: PipelineState;
		muted?: boolean;
		micLabel?: string;
		orbLevel?: number;
		speak?: boolean;
		/** True while this surface is recording a spoken question. */
		capturing?: boolean;
		/** The HUD's live state colour, so both surfaces move together. */
		accent?: string;
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

	/** The button's tooltip: a real fault if there is one, else what it does. */
	function micError(): string {
		const trouble = micLabel && /BLOCKED|NO MICROPHONE|UNAVAILABLE/.test(micLabel);
		return trouble ? micLabel : 'Press, speak, and pause when you are done';
	}

	function openAndClose(id: string): void {
		onOpen(id);
		sidebarOpen = false;
	}

	onMount(() => {
		composer?.focus();
	});
</script>

<section
	class="chat"
	data-testid="chat-panel"
	data-state={turnState}
	style={accent ? `--jv-state-accent: ${accent}` : undefined}
>
	<!-- The same two pieces of chrome every other surface draws, so chat mode
	     reads as part of the HUD rather than a plain document pasted over it:
	     the faint scan grid, and the corner brackets. Both are shared utilities
	     from chrome.css and inherit the live state accent. -->
	<div class="jv-grid" aria-hidden="true"></div>
	<span class="jv-bracket tl" aria-hidden="true"></span>
	<span class="jv-bracket br" aria-hidden="true"></span>
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
				title="Whether replies are also spoken out loud"
				onclick={onToggleSpeak}
			>
				{speak ? 'SPEAKS' : 'SILENT'}
			</button>
			<ModeToggle chat={true} onToggle={onToggleMode} />
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
					<span class="empty-mark" aria-hidden="true"></span>
					<p class="empty-head">Good evening.</p>
					<p class="empty-sub">
						Ask by typing, or hold the microphone to speak. Nothing is heard
						until you press it.
					</p>
				</div>
			{:else}
				{#each messages as message (message.id)}
					<ChatMessage {message} />
				{/each}
			{/if}
		</div>

		<form class="composer" onsubmit={submit}>
			<!--
			  Press to speak. The orb's button is a mute switch over an always-on
			  VAD, which is right across a room and wrong at a keyboard; here the
			  same hardware is driven the other way round. Nothing leaves the
			  browser until this is pressed, so it is the privacy boundary too and
			  chat mode needs no separate mute.
			-->
			<button
				type="button"
				class="mic"
				class:live={capturing}
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
		/*
		 * The accent is LIVE — it tracks the pipeline state exactly as the orb's
		 * does, so a turn being thought about looks the same on both surfaces.
		 * The line and dim tokens are re-derived from it, which is what lets the
		 * shared .jv-* utilities pick the state colour up by inheritance.
		 */
		--jv-state-accent: var(--jv-accent);
		--jv-state-line: color-mix(in srgb, var(--jv-state-accent) 30%, transparent);
		--jv-state-line-soft: color-mix(in srgb, var(--jv-state-accent) 13%, transparent);
		--jv-line: var(--jv-state-line);
		--jv-line-soft: var(--jv-state-line-soft);
		--jv-grid-mask: radial-gradient(ellipse 90% 70% at 50% 30%, var(--jv-bg) 35%, transparent 92%);
		--jv-bracket-size: clamp(18px, 2.6vw, 34px);
		--jv-bracket-inset: 10px;

		position: relative;
		display: grid;
		grid-template-columns: 17rem minmax(0, 1fr);
		height: 100vh;
		height: 100dvh;
		min-height: 0;
		font-family: var(--jv-font-body);
		color: var(--jv-text);
		background:
			radial-gradient(
				ellipse 60% 45% at 78% 0%,
				color-mix(in srgb, var(--jv-state-accent) 10%, transparent),
				transparent 72%
			),
			var(--jv-bg);
		transition: background var(--jv-dur-slow) ease;
	}
	/* The chrome sits behind everything and takes no clicks. */
	.chat > :global(.jv-grid),
	.chat > :global(.jv-bracket) {
		position: absolute;
		pointer-events: none;
	}
	.past,
	.thread {
		position: relative;
		z-index: 1;
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
		padding: var(--jv-space-1) var(--jv-space-3);
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
		gap: var(--jv-rule-live);
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
		gap: var(--jv-space-1) var(--jv-space-2);
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
		color: var(--jv-state-accent);
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
		border-bottom: 1px solid var(--jv-state-line-soft);
		background: linear-gradient(
			180deg,
			color-mix(in srgb, var(--jv-state-accent) 6%, transparent),
			transparent
		);
	}
	.mini-orb {
		width: var(--jv-space-6);
		height: var(--jv-space-6);
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
		padding: var(--jv-space-1) var(--jv-space-3);
		cursor: pointer;
		white-space: nowrap;
	}
	.chip:hover {
		color: var(--jv-state-accent);
		border-color: var(--jv-state-line);
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
		padding: var(--jv-space-1) var(--jv-space-2);
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
		max-width: calc(var(--jv-space-7) * 9.33333);
	}
	.empty-mark {
		display: block;
		width: calc(var(--jv-space-1) * 8.5);
		height: calc(var(--jv-space-1) * 8.5);
		margin: 0 auto var(--jv-space-4);
		border: 1px solid var(--jv-state-line);
		border-radius: 50%;
		box-shadow: 0 0 calc(var(--jv-space-1) * 4.5) color-mix(in srgb, var(--jv-state-accent) 30%, transparent),
			inset 0 0 var(--jv-radius-lg) color-mix(in srgb, var(--jv-state-accent) 22%, transparent);
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
		min-height: var(--jv-space-7);
		max-height: calc(var(--jv-space-7) * 4);
		/* `field-sizing` grows the box with its content where it is supported and
		   is ignored where it is not, which is why max-height is set above and
		   the rows attribute is 1: the fallback is a one-line box that scrolls. */
		field-sizing: content;
		padding: var(--jv-space-3) var(--jv-space-3);
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
		padding: var(--jv-space-3) var(--jv-space-4);
		cursor: pointer;
		white-space: nowrap;
		transition: background var(--jv-dur-fast) var(--jv-ease-out),
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
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-2);
		color: var(--jv-text-dim);
		background: transparent;
		border: 1px solid var(--jv-state-line-soft);
	}
	.mic:hover {
		color: var(--jv-state-accent);
		border-color: var(--jv-state-line);
		background: var(--jv-wash);
	}
	.mic-dot {
		width: calc(var(--jv-space-1) * 1.75);
		height: calc(var(--jv-space-1) * 1.75);
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
		background: color-mix(in srgb, var(--jv-danger) 12%, transparent);
	}
	.mic.live .mic-dot {
		background: var(--jv-danger);
		opacity: 1;
		box-shadow: 0 0 calc(var(--jv-space-1) * 2) var(--jv-danger);
		animation: mic-live var(--jv-dur-enter) ease-in-out infinite;
	}
	@keyframes mic-live {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0.3;
		}
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
			width: min(calc(var(--jv-space-7) * 6), 84vw);
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
		.past,
		.chat {
			transition: none;
		}
		.mic.live .mic-dot {
			animation: none;
		}
	}
</style>
