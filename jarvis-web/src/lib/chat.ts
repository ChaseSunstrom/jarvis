// The chat transcript, as data.
//
// Everything here is a pure function over an immutable message list: the
// component holds `let messages = $state<ChatMessage[]>([])` and reassigns it,
// which is what makes Svelte 5 redraw and what makes this file testable in
// plain Node. No DOM, no runes, no socket.
//
// The shape it builds is the whole point of chat mode. A turn is not one
// paragraph that appears at the end — it is a sequence: the model thinks, calls
// a tool, reads the result, calls another, then speaks. All of that belongs
// inside the assistant's message, in the order it happened, which is why a
// message carries `thinking` and `tools` rather than the page keeping them in
// parallel arrays it would have to correlate later.

import type { ArchivedConversation, ArchivedTurn } from './jarvisClient';
import type { ToolCallEvent } from './pipeline';

export type ChatRole = 'user' | 'assistant';

export type ToolState = 'running' | 'ok' | 'failed';

export interface ChatToolCall {
	/** Stable across the start and end events for the same call. */
	key: string;
	name: string;
	arguments: Record<string, unknown>;
	state: ToolState;
	error?: string | null;
	durationMs?: number;
}

export interface ChatMessage {
	id: string;
	role: ChatRole;
	content: string;
	/** The model's reasoning, if it reasons out loud. Never spoken. */
	thinking: string;
	tools: ChatToolCall[];
	at: number;
	/** True while this assistant message is still being streamed. */
	pending: boolean;
	error?: string | null;
}

/**
 * Ids for messages the browser created.
 *
 * A counter and not `crypto.randomUUID()`: this runs in the SSR pass too, and
 * the ids only have to be unique within one page's transcript — they are keys
 * for an `{#each}`, not anything anyone stores.
 */
let sequence = 0;
function nextId(prefix: string): string {
	sequence += 1;
	return `${prefix}-${sequence}`;
}

/** Reset the id counter. Tests only — nothing in the app needs it. */
export function resetIds(): void {
	sequence = 0;
}

export function userMessage(text: string, at = Date.now()): ChatMessage {
	return {
		id: nextId('u'),
		role: 'user',
		content: text,
		thinking: '',
		tools: [],
		at,
		pending: false
	};
}

/**
 * The empty assistant message a turn streams into.
 *
 * Added the moment the question is sent rather than when the first token
 * arrives, because the gap between those two is the part of a turn that feels
 * broken: nine seconds of tool calls with nothing on screen reads as a page
 * that has stopped working. The placeholder is where the tool rows and the
 * reasoning land while there is still no text.
 */
export function assistantPlaceholder(at = Date.now()): ChatMessage {
	return {
		id: nextId('a'),
		role: 'assistant',
		content: '',
		thinking: '',
		tools: [],
		at,
		pending: true
	};
}

/** The index of the message a turn is currently streaming into, or -1. */
function pendingIndex(messages: ChatMessage[]): number {
	for (let i = messages.length - 1; i >= 0; i--) {
		if (messages[i].role === 'assistant' && messages[i].pending) return i;
	}
	return -1;
}

/**
 * Apply a change to the message being streamed into.
 *
 * Every mutation below goes through here, so a stray event that arrives after
 * a turn has settled — a late `intent-tool-end` from a run the user abandoned,
 * anything on a socket that reconnected — is dropped instead of being appended
 * to whatever the last message happens to be. That was the failure mode worth
 * designing out: tool rows from a cancelled turn attaching themselves to the
 * next answer.
 */
function patchPending(
	messages: ChatMessage[],
	change: (message: ChatMessage) => ChatMessage
): ChatMessage[] {
	const index = pendingIndex(messages);
	if (index === -1) return messages;
	const next = messages.slice();
	next[index] = change(next[index]);
	return next;
}

export function withDelta(messages: ChatMessage[], delta: string): ChatMessage[] {
	if (!delta) return messages;
	return patchPending(messages, (m) => ({ ...m, content: m.content + delta }));
}

export function withThinking(messages: ChatMessage[], delta: string): ChatMessage[] {
	if (!delta) return messages;
	return patchPending(messages, (m) => ({ ...m, thinking: m.thinking + delta }));
}

/**
 * The key that ties a tool's start event to its end event.
 *
 * Round and index, not the name: a turn that calls `get_state` three times in
 * one round is ordinary, and keying on the name alone would collapse the three
 * rows into one that finishes twice.
 */
export function toolKey(call: ToolCallEvent): string {
	return `${call.round ?? 0}:${call.index ?? 0}:${call.name}`;
}

export function withToolStart(
	messages: ChatMessage[],
	call: ToolCallEvent
): ChatMessage[] {
	const key = toolKey(call);
	return patchPending(messages, (m) => ({
		...m,
		tools: [
			...m.tools.filter((t) => t.key !== key),
			{
				key,
				name: call.name,
				arguments: (call.arguments ?? {}) as Record<string, unknown>,
				state: 'running' as const
			}
		]
	}));
}

export function withToolEnd(messages: ChatMessage[], call: ToolCallEvent): ChatMessage[] {
	const key = toolKey(call);
	return patchPending(messages, (m) => {
		const known = m.tools.some((t) => t.key === key);
		// An end with no start — a reconnect mid-turn, an older core — still
		// deserves a row. Losing it would show a turn that touched the house as
		// one that did not.
		const tools = known
			? m.tools
			: [
					...m.tools,
					{
						key,
						name: call.name,
						arguments: (call.arguments ?? {}) as Record<string, unknown>,
						state: 'running' as const
					}
				];
		return {
			...m,
			tools: tools.map((tool) =>
				tool.key === key
					? {
							...tool,
							state: (call.ok === false ? 'failed' : 'ok') as ToolState,
							error: call.error ?? null,
							durationMs: call.duration_ms ?? 0
						}
					: tool
			)
		};
	});
}

/**
 * The turn's final text, from `intent-end`.
 *
 * Only replaces what streamed if the deltas produced nothing — a backend that
 * sends the whole answer at the end rather than token by token. Overwriting a
 * streamed message with the identical final text would make the whole
 * paragraph flash on every turn.
 */
export function withFinal(messages: ChatMessage[], text: string): ChatMessage[] {
	if (!text) return messages;
	return patchPending(messages, (m) => (m.content ? m : { ...m, content: text }));
}

/** Mark the streaming message done. Idempotent — `run-end` can arrive twice. */
export function settled(messages: ChatMessage[]): ChatMessage[] {
	return patchPending(messages, (m) => ({ ...m, pending: false }));
}

/**
 * The turn failed.
 *
 * Recorded on the message rather than in a page-level banner, because by the
 * time a second question has been asked a banner is about neither of them.
 * Scrolling back has to show which turn broke.
 */
export function withError(
	messages: ChatMessage[],
	code: string,
	message: string
): ChatMessage[] {
	const patched = patchPending(messages, (m) => ({
		...m,
		pending: false,
		error: `${code}: ${message}`
	}));
	if (patched !== messages) return patched;
	// Nothing was streaming — the run died before its placeholder existed.
	return [
		...messages,
		{
			...assistantPlaceholder(),
			pending: false,
			error: `${code}: ${message}`
		}
	];
}

/** Rebuild a transcript from a stored conversation, for reopening one. */
export function fromArchive(conversation: ArchivedConversation | null): ChatMessage[] {
	if (!conversation?.turns?.length) return [];
	return conversation.turns.map((turn: ArchivedTurn, index: number) => ({
		id: `${conversation.id}-${index}`,
		role: turn.role === 'user' ? ('user' as const) : ('assistant' as const),
		content: String(turn.content ?? ''),
		thinking: String(turn.thinking ?? ''),
		tools: (turn.tool_calls ?? []).map((call, position) => ({
			key: `${conversation.id}-${index}-${position}`,
			name: String(call.name ?? 'tool'),
			arguments: (call.arguments ?? {}) as Record<string, unknown>,
			// A stored call has already happened, so it is never 'running'.
			state: (call.ok === false ? 'failed' : 'ok') as ToolState,
			error: call.error ?? null
		})),
		at: (turn.timestamp ?? 0) * 1000,
		pending: false
	}));
}

/** A one-line rendering of a tool's arguments, for a row that stays one line. */
export function summariseArgs(args: Record<string, unknown> | undefined): string {
	const parts: string[] = [];
	for (const [key, value] of Object.entries(args ?? {})) {
		if (value === null || value === undefined || value === '') continue;
		const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
		parts.push(`${key}: ${text.length > 40 ? `${text.slice(0, 39)}…` : text}`);
		if (parts.length >= 3) break;
	}
	return parts.join(' · ');
}

/**
 * How long ago, in the fewest characters that are still true.
 *
 * A sidebar row has about eight characters for this, and "14:32" is useless
 * without a date while "2026-08-17 14:32" does not fit. Relative wins until it
 * stops being meaningful, which is about a week.
 */
export function relativeTime(timestampSeconds: number, now = Date.now()): string {
	const ms = now - timestampSeconds * 1000;
	if (!Number.isFinite(ms) || ms < 0) return 'now';
	const minutes = Math.floor(ms / 60_000);
	if (minutes < 1) return 'now';
	if (minutes < 60) return `${minutes}m`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) return `${hours}h`;
	const days = Math.floor(hours / 24);
	if (days < 7) return `${days}d`;
	return new Date(timestampSeconds * 1000).toISOString().slice(0, 10);
}
