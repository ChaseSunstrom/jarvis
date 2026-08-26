<script lang="ts">
	/**
	 * Tier-3 actions waiting on a human.
	 *
	 * Global rather than a page, and that is the whole design. An approval is
	 * raised by whatever the assistant was asked to do — it can arrive while you
	 * are on /devices, or mid-sentence on the HUD — and a request that expires
	 * unseen is indistinguishable from Jarvis ignoring you. So this lives in the
	 * layout and puts itself in front of whatever is on screen.
	 *
	 * The console had no way to approve anything at all before this: the gate
	 * fired, the model was told to wait, and the only surface that could say yes
	 * was the phone.
	 *
	 * What it deliberately does NOT do is decide anything. The request was pinned
	 * server-side when it was raised — fuzzy targets already resolved to concrete
	 * entity ids — so what runs is what is shown here, and a second click cannot
	 * run it twice because jarvis-core pops the request before executing.
	 */
	import { Button } from '$lib/ui';
	import { onMount } from 'svelte';
	import { describeError, type Connection } from '$lib/connection';
	import { toasts } from '$lib/toast';
	import type { BusEvent, PendingApproval, Subscription } from '$lib/jarvisClient';

	let { conn }: { conn: Connection | null } = $props();

	let pending = $state<PendingApproval[]>([]);
	let busy = $state('');
	let err = $state('');
	/**
	 * What the human has typed, per request, for a request that is a QUESTION.
	 *
	 * Keyed by request id rather than held on the request object: the request is
	 * replaced wholesale whenever the server re-announces it, and a draft answer
	 * that vanished mid-sentence because a reconnect refreshed the list would be
	 * infuriating in exactly the moment somebody is trying to reply.
	 */
	let answers = $state<Record<string, string>>({});
	/** Ticks once a second so the countdown is honest. */
	let now = $state(Date.now());

	const idOf = (req: PendingApproval): string => req.request_id ?? req.id ?? '';

	function upsert(req: PendingApproval): void {
		const id = idOf(req);
		if (!id) return;
		pending = [...pending.filter((p) => idOf(p) !== id), req];
	}

	function drop(id: string): void {
		pending = pending.filter((p) => idOf(p) !== id);
	}

	/** Seconds left, or null when the server gave no expiry. */
	function secondsLeft(req: PendingApproval): number | null {
		if (!req.expires_at) return null;
		// `expires_at` is epoch SECONDS from Python's time.time().
		return Math.max(0, Math.round(req.expires_at - now / 1000));
	}

	/** The arguments, rendered compactly — this is what the human is agreeing to. */
	function summarise(req: PendingApproval): string {
		const args = req.arguments ?? {};
		const parts = Object.entries(args)
			.filter(([, v]) => v !== null && v !== undefined && v !== '')
			.map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : JSON.stringify(v)}`);
		return parts.join(' · ') || 'no arguments';
	}

	/**
	 * A question rather than an action: the server named a writable argument.
	 *
	 * Read off the request, not inferred from the tool's name, because the
	 * console does not hold a tool registry and a name-based rule would be wrong
	 * for the first tool anybody adds.
	 */
	const isQuestion = (req: PendingApproval): boolean => Boolean(req.answerable);

	/** What the question is, for a request that is one. */
	function questionOf(req: PendingApproval): string {
		const args = (req.arguments ?? {}) as Record<string, unknown>;
		const asked = args.question ?? args.prompt ?? args.text;
		return typeof asked === 'string' && asked ? asked : req.description || req.tool;
	}

	const choicesOf = (req: PendingApproval): string[] =>
		Array.isArray(req.choices) ? req.choices.map(String) : [];

	async function resolve(
		req: PendingApproval,
		approved: boolean,
		answer?: string
	): Promise<void> {
		const id = idOf(req);
		if (!conn || !id) return;
		busy = id;
		err = '';
		try {
			const result = await conn.client.resolveApproval(id, approved, answer);
			drop(id);
			delete answers[id];
			if (result?.status === 'error') {
				// Expired or already used. Saying so is better than a silent
				// disappearance, because the action did NOT happen.
				toasts.error(`${req.tool} was not run`, result.error ?? 'the request had expired');
			} else if (isQuestion(req)) {
				toasts.success(approved ? 'Answer sent' : `Dismissed ${req.tool}`);
			} else {
				toasts.success(approved ? `Approved ${req.tool}` : `Denied ${req.tool}`);
			}
		} catch (e) {
			err = describeError(e);
			toasts.error(`Could not answer for ${req.tool}`, describeError(e));
		} finally {
			busy = '';
		}
	}

	onMount(() => {
		const ticker = setInterval(() => {
			now = Date.now();
			// Drop what the server has already expired, so the card cannot sit at
			// "0s" offering a button that can no longer do anything.
			pending = pending.filter((p) => (secondsLeft(p) ?? 1) > 0);
		}, 1000);
		return () => clearInterval(ticker);
	});

	// Re-subscribes whenever the connection is replaced (a reconnect gives a new
	// client), and catches up on anything raised while the socket was down.
	$effect(() => {
		const connection = conn;
		if (!connection) return;
		let disposed = false;
		const subs: Subscription[] = [];

		(async () => {
			try {
				const current = await connection.client.pendingApprovals();
				if (!disposed) for (const req of current) upsert(req);
			} catch {
				// An older backend has no such service. The live events below
				// still work, so this is not worth an error banner.
			}
			try {
				subs.push(
					await connection.client.subscribeEvents((event: BusEvent) => {
						upsert(event.data as PendingApproval);
					}, 'jarvis_approval_required')
				);
				subs.push(
					await connection.client.subscribeEvents((event: BusEvent) => {
						// Answered somewhere else — the phone, a script, another tab.
						drop(String(event.data?.request_id ?? event.data?.id ?? ''));
					}, 'jarvis_approval_resolved')
				);
			} catch (e) {
				if (!disposed) err = describeError(e);
			}
		})();

		return () => {
			disposed = true;
			for (const sub of subs) void sub.unsubscribe();
		};
	});
</script>

{#if pending.length}
	<section class="approvals" data-testid="approvals" aria-live="assertive">
		<div class="head">
			<span>Held · {pending.length} thing{pending.length === 1 ? '' : 's'} waiting on you · asks before it runs</span>
		</div>

		{#if err}<p class="err" data-testid="approval-error" role="alert">{err}</p>{/if}

		{#each pending as req (idOf(req))}
			{@const left = secondsLeft(req)}
			{@const id = idOf(req)}
			{#if isQuestion(req)}
				<!--
				  A question, not an action. Same gate, same expiry, same
				  single-use guarantee — see `ask_user` in jarvis-core — but
				  "APPROVE / DENY" is the wrong pair of words for "which lamp did
				  you mean?", so it gets the shape of the thing it is.
				-->
				<div class="req question" class:tainted={req.tainted} data-testid="question-{req.tool}">
					<div class="what">
						<b data-testid="question-text">{questionOf(req)}</b>
						{#if req.tainted}
							<!--
							  Where the words came from. This turn read something a
							  stranger wrote before it composed this question, and
							  the question is rendered verbatim — so the sentence
							  above may have been written by whatever it read
							  rather than by Jarvis. Nothing is blocked; the tier
							  already decided what may run. The human is simply
							  told, which is the only defence that works against a
							  sentence that is legitimate half the time.
							-->
							<span class="desc warn" data-testid="question-tainted">
								This turn read something from outside your house before asking. Treat the
								wording above as untrusted — never type a password or a code into it.
							</span>
						{:else}
							<span class="desc">Jarvis is waiting for your answer</span>
						{/if}
					</div>
					{#if left !== null}
						<span class="left" data-testid="approval-expiry-{req.tool}">{left}s</span>
					{/if}
					{#if choicesOf(req).length}
						<div class="choices" data-testid="question-choices">
							{#each choicesOf(req) as choice (choice)}
								<Button
									variant="approve"
									testid="answer-choice-{choice}"
									disabled={busy === id}
									onclick={() => resolve(req, true, choice)}
								>
									{choice}
								</Button>
							{/each}
						</div>
					{:else}
						<input
							type="text"
							class="answer"
							placeholder="Type your answer"
							aria-label={questionOf(req)}
							data-testid="answer-input"
							disabled={busy === id}
							value={answers[id] ?? ''}
							oninput={(e) => (answers[id] = (e.currentTarget as HTMLInputElement).value)}
							onkeydown={(e) => {
								if (e.key === 'Enter' && (answers[id] ?? '').trim()) {
									resolve(req, true, answers[id].trim());
								}
							}}
						/>
						<Button
							variant="approve"
							testid="answer-send"
							disabled={busy === id || !(answers[id] ?? '').trim()}
							onclick={() => resolve(req, true, (answers[id] ?? '').trim())}
						>
							SEND
						</Button>
					{/if}
					<Button variant="danger" testid="answer-dismiss"
						disabled={busy === id}
						onclick={() => resolve(req, false)}
					>
						DISMISS
					</Button>
				</div>
			{:else}
				<div class="req" class:tainted={req.tainted} data-testid="approval-{req.tool}">
					<div class="what">
						<b>{req.tool}</b>
						{#if req.tainted}
							<span class="desc warn" data-testid="approval-tainted">
								Raised by a turn that read something from outside your house.
							</span>
						{/if}
						{#if req.description}<span class="desc">{req.description}</span>{/if}
						<span class="args" data-testid="approval-args-{req.tool}">{summarise(req)}</span>
					</div>
					{#if left !== null}
						<span class="left" data-testid="approval-expiry-{req.tool}">{left}s</span>
					{/if}
					<Button
						variant="primary"
						testid="approve-{req.tool}"
						disabled={busy === id}
						onclick={() => resolve(req, true)}
					>
						APPROVE
					</Button>
					<Button testid="deny-{req.tool}"
						disabled={busy === id}
						onclick={() => resolve(req, false)}
					>
						DENY
					</Button>
				</div>
			{/if}
		{/each}
	</section>
{/if}

<style>
	/*
	 * Reactor II's held bar: a flat panel with the warn colour as an INSET rule
	 * on its left — a box-shadow, not a border, so it never moves the layout —
	 * the label in the warn colour, what is held in the bright text, its
	 * arguments in mono, and APPROVE as the one filled control on the screen.
	 */
	.approvals {
		position: sticky;
		top: 0;
		z-index: 40;
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-warn), var(--jv-elev-panel);
		padding: var(--jv-space-3) var(--jv-space-4) var(--jv-space-3) var(--jv-space-5);
		margin-bottom: var(--jv-space-4);
		/* Waiting on you: the warn rule pulses slowly until answered (M53). */
		animation:
			jv-rise var(--jv-dur-base) var(--jv-ease-out) both,
			held-pulse var(--jv-dur-blink) var(--jv-ease-in-out) infinite alternate;
	}
	@keyframes held-pulse {
		from {
			box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-warn), var(--jv-elev-panel);
		}
		to {
			box-shadow: inset var(--jv-rule-live) 0 0 transparent, var(--jv-elev-panel);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.approvals {
			animation: none;
		}
	}
	.head {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-warn);
		margin-bottom: var(--jv-space-2);
	}
	.req {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
		padding: var(--jv-space-3) 0;
		border-top: 1px solid var(--jv-line-hair);
	}
	/* A question is the assistant asking, not the assistant about to act. */
	.req.question {
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
		padding-left: var(--jv-space-3);
	}
	/* Louder than the ordinary accent, quieter than a denial: provenance. */
	.req.tainted {
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-warn);
		padding-left: var(--jv-space-3);
	}
	.what {
		flex: 1 1 14rem;
		min-width: 0;
	}
	.what b {
		display: block;
		color: var(--jv-text-bright);
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-sm);
	}
	.desc {
		display: block;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		overflow-wrap: anywhere;
	}
	.desc.warn {
		color: var(--jv-warn);
	}
	.args {
		display: block;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		overflow-wrap: anywhere;
	}
	.left {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		font-variant-numeric: tabular-nums;
		color: var(--jv-warn);
	}
	.answer {
		flex: 1 1 14rem;
		min-width: 0;
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-3);
	}
	.answer:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.choices {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
	}
	.err {
		margin: 0 0 var(--jv-space-2);
		font-size: var(--jv-fs-xs);
		color: var(--jv-danger-text);
	}
</style>
