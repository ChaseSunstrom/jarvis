<script lang="ts">
	/**
	 * One turn in the transcript, with its working shown.
	 *
	 * The interesting part of a Jarvis turn is rarely the sentence at the end —
	 * it is which tools it touched and what it decided before touching them.
	 * Both are drawn inline, in the order they happened, inside the message they
	 * belong to. A separate activity panel would be the same information in a
	 * place that cannot survive the next question.
	 *
	 * Reasoning is COLLAPSED by default and never styled as prose. It is not the
	 * answer, it is not spoken, and a model's deliberation presented at the same
	 * weight as its reply is how a chat UI teaches people to read a guess as a
	 * conclusion.
	 */
	import { summariseArgs, type ChatMessage } from '$lib/chat';

	let { message }: { message: ChatMessage } = $props();

	// Collapsed on arrival, and remembered per message once a reader opens it —
	// a block that re-collapsed on every delta would be unreadable during
	// exactly the turn you opened it to watch.
	let open = $state(false);

	const isUser = $derived(message.role === 'user');
	const running = $derived(message.tools.some((t) => t.state === 'running'));
	const failed = $derived(message.tools.filter((t) => t.state === 'failed').length);
	// The caret only belongs on a message that is still being written AND has
	// nothing else moving: a blinking cursor beside a spinning tool row reads
	// as two different things happening.
	const showCaret = $derived(message.pending && !running);
</script>

<article
	class="msg"
	class:user={isUser}
	class:assistant={!isUser}
	data-testid="chat-message"
	data-role={message.role}
	data-pending={message.pending ? 'true' : 'false'}
>
	<span class="who" aria-hidden="true">{isUser ? 'YOU' : 'JARVIS'}</span>

	<div class="body">
		{#if message.thinking}
			<details
				class="thinking"
				bind:open
				data-testid="chat-thinking"
			>
				<summary>
					<span class="spark" aria-hidden="true"></span>
					<!-- Counted, not measured in tokens: "reasoned for 412 characters"
					     is a number nobody can use. The word count is a length a
					     reader can decide whether to open. -->
					REASONING · {message.thinking.trim().split(/\s+/).length} words
				</summary>
				<p>{message.thinking}</p>
			</details>
		{/if}

		{#if message.tools.length}
			<ul class="tools" data-testid="chat-tools" aria-label="Tools this turn used">
				{#each message.tools as tool (tool.key)}
					<li class={tool.state} data-testid="chat-tool-{tool.name}">
						<span class="dot" aria-hidden="true"></span>
						<span class="name">{tool.name}</span>
						<span class="args">{summariseArgs(tool.arguments)}</span>
						{#if tool.state === 'running'}
							<span class="meta" aria-label="running">…</span>
						{:else if tool.state === 'failed'}
							<span class="meta err">{tool.error ?? 'failed'}</span>
						{:else if tool.durationMs}
							<span class="meta">{tool.durationMs}ms</span>
						{:else}
							<span class="meta">done</span>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}

		{#if message.content}
			<p class="text" data-testid="chat-text">{message.content}{#if showCaret}<span
						class="caret"
						aria-hidden="true"
					></span>{/if}</p>
		{:else if message.pending}
			<p class="text waiting" data-testid="chat-waiting">
				{#if running}
					<span class="sr">Working</span>
				{:else}
					<span class="sr">Thinking</span><span class="caret" aria-hidden="true"></span>
				{/if}
			</p>
		{:else if message.role === 'assistant'}
			<!--
				A settled assistant turn with no text at all.

				jarvis-core no longer produces one — a turn that would have been
				empty now falls back to a sentence — but an older backend does,
				and this branch did not exist: both arms above were false and
				the bubble rendered nothing, leaving a permanent blank under a
				collapsed "REASONING · N words". A blank is indistinguishable
				from a client that lost the message, so it is worth saying.
			-->
			<p class="text empty" data-testid="chat-empty">
				No answer came back for this one.{#if message.thinking}
					Only reasoning — open it above.{/if}
			</p>
		{/if}

		{#if message.error}
			<p class="error" role="alert" data-testid="chat-error">{message.error}</p>
		{/if}

		{#if message.memoryUsed?.length}
			<!--
			  Why this answer, in the only honest form: the notes the model was
			  actually given. Collapsed, because it is provenance rather than
			  content — and present, because personalisation nobody can inspect
			  is indistinguishable from a machine making things up about them.
			-->
			<details class="why" data-testid="chat-memory-used">
				<summary>WHY THIS ANSWER · {message.memoryUsed.length} remembered</summary>
				<ul>
					{#each message.memoryUsed as note, index (index)}
						<li>{note}</li>
					{/each}
				</ul>
			</details>
		{/if}
	</div>
</article>

<style>
	.why {
		margin-top: var(--jv-space-2);
	}
	.why summary {
		color: var(--jv-text-faint);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-chrome);
		cursor: pointer;
	}
	.why ul {
		margin: var(--jv-space-1) 0 0;
		padding-left: var(--jv-space-4);
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-xs);
	}
	.msg {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--jv-space-1);
		padding: var(--jv-space-3) var(--jv-space-4);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		background: var(--jv-panel);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	/* The user's own words sit inset and cooler; Jarvis gets the lit edge. The
	   two must be distinguishable without reading the label. */
	.msg.user {
		background: transparent;
		border-color: transparent;
		border-left: 1px solid var(--jv-line-soft);
		border-radius: 0;
		margin-left: auto;
		max-width: min(calc(var(--jv-space-7) * 15.3333), 88%);
	}
	.msg.assistant {
		border-left: 2px solid var(--jv-accent);
		box-shadow: var(--jv-elev-panel);
	}

	.who {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-faint);
	}
	.msg.assistant .who {
		color: var(--jv-accent);
	}

	.body {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
		min-width: 0;
	}

	.text {
		margin: 0;
		font-size: var(--jv-fs-md);
		line-height: 1.55;
		color: var(--jv-text);
		/* Model output has paragraphs and lists in it. Collapsing them into one
		   run of text is the single thing that makes a chat surface look wrong. */
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.msg.assistant .text {
		color: var(--jv-text-bright);
	}
	.text.empty {
		color: var(--jv-text-faint);
		font-style: italic;
	}
	.text.waiting {
		color: var(--jv-text-faint);
		min-height: var(--jv-rel-line);
	}
	.sr {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
	}

	.caret {
		display: inline-block;
		width: 0.5ch;
		height: var(--jv-rel-caret);
		margin-left: 0.15em;
		vertical-align: -0.15em;
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-sm);
		animation: caret var(--jv-dur-enter) steps(2) infinite;
	}
	@keyframes caret {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0;
		}
	}

	/* --- reasoning --- */
	.thinking {
		border: 1px dashed var(--jv-line-soft);
		border-radius: var(--jv-radius-sm);
		background: var(--jv-field);
	}
	.thinking summary {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		padding: var(--jv-space-2) var(--jv-space-3);
		cursor: pointer;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		color: var(--jv-text-faint);
		list-style: none;
	}
	.thinking summary::-webkit-details-marker {
		display: none;
	}
	.thinking summary:hover {
		color: var(--jv-amber);
	}
	.thinking summary:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.spark {
		width: var(--jv-radius-md);
		height: var(--jv-radius-md);
		border-radius: 50%;
		background: var(--jv-amber);
		box-shadow: 0 0 calc(var(--jv-space-1) * 2) var(--jv-amber);
	}
	.thinking p {
		margin: 0;
		padding: 0 var(--jv-space-3) var(--jv-space-3);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		line-height: 1.5;
		color: var(--jv-text-faint);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		max-height: calc(var(--jv-space-7) * 7.33333);
		overflow-y: auto;
	}

	/* --- tool rows --- */
	.tools {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--jv-rule-live);
	}
	.tools li {
		display: grid;
		grid-template-columns: 10px minmax(0, auto) minmax(0, 1fr) auto;
		align-items: baseline;
		gap: var(--jv-space-2);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		animation: jv-rise var(--jv-dur-fast) var(--jv-ease-out) both;
	}
	.dot {
		width: var(--jv-radius-md);
		height: var(--jv-radius-md);
		border-radius: 50%;
		background: var(--jv-line);
		justify-self: center;
	}
	li.running .dot {
		background: var(--jv-accent);
		animation: jv-tool-pulse var(--jv-dur-enter) ease-in-out infinite;
	}
	li.ok .dot {
		background: var(--jv-ok);
	}
	li.failed .dot {
		background: var(--jv-danger);
	}
	.name {
		color: var(--jv-text);
		font-weight: 600;
	}
	.args,
	.meta {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.meta {
		font-variant-numeric: tabular-nums;
		color: var(--jv-text-faint);
	}
	.meta.err {
		color: var(--jv-danger-text);
	}
	@keyframes jv-tool-pulse {
		0%,
		100% {
			opacity: 1;
			transform: scale(1);
		}
		50% {
			opacity: 0.35;
			transform: scale(0.7);
		}
	}

	.error {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-danger-text);
	}

	@media (prefers-reduced-motion: reduce) {
		.msg,
		.tools li {
			animation: none;
		}
		li.running .dot,
		.caret {
			animation: none;
		}
	}
</style>
