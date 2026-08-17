// The conversation-history commands, once.
//
// Two clients speak the same socket for different reasons — `PipelineClient`
// owns a voice or text run, `JarvisClient` owns everything else — and chat mode
// is the first feature that needs the same four commands from both: the HUD
// already holds a `PipelineClient` and opening a second socket to list a
// sidebar would be absurd, while the console's palette and pages hold a
// `JarvisClient`.
//
// So the wire strings live here and both clients delegate. A command renamed in
// one place is renamed everywhere, which is the failure this avoids: a chat
// sidebar that works on the HUD and 404s in the console.

import type { ArchivedConversation, ConversationSummary } from './jarvisClient';

/** Anything that can send a command frame and await its result. */
export type CommandFn = <T = any>(payload: Record<string, any>) => Promise<T>;

export const CMD_LIST = 'jarvis/conversation/list';
export const CMD_GET = 'jarvis/conversation/get';
export const CMD_DELETE = 'jarvis/conversation/delete';
export const CMD_RENAME = 'jarvis/conversation/rename';

/** Past conversations, most recent first. Summaries only — no message bodies. */
export async function listConversations(
	command: CommandFn
): Promise<ConversationSummary[]> {
	const result = await command<{ conversations?: ConversationSummary[] }>({
		type: CMD_LIST
	});
	return Array.isArray(result?.conversations) ? result.conversations : [];
}

/** One conversation in full, with each turn's reasoning and tool calls. */
export async function getConversation(
	command: CommandFn,
	conversationId: string
): Promise<ArchivedConversation | null> {
	const result = await command<{ conversation?: ArchivedConversation }>({
		type: CMD_GET,
		conversation_id: conversationId
	});
	return result?.conversation ?? null;
}

/** Forget it, in the model's memory and in the archive alike. */
export async function deleteConversation(
	command: CommandFn,
	conversationId: string
): Promise<boolean> {
	const result = await command<{ deleted?: boolean }>({
		type: CMD_DELETE,
		conversation_id: conversationId
	});
	return Boolean(result?.deleted);
}

/** A name of your own, instead of the conversation's first sentence. */
export async function renameConversation(
	command: CommandFn,
	conversationId: string,
	title: string
): Promise<boolean> {
	const result = await command<{ renamed?: boolean }>({
		type: CMD_RENAME,
		conversation_id: conversationId,
		title
	});
	return Boolean(result?.renamed);
}
