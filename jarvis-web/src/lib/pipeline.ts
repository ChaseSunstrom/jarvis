// Framework-agnostic client for Home Assistant's assist_pipeline/run WebSocket
// API. No browser globals — unit-testable in plain Node.
//
// The transport is injected as a `send` function; incoming socket messages are
// fed to `handleMessage()`. Message ids are monotonically increasing integers
// per connection.

export type PipelineState = 'idle' | 'listening' | 'thinking' | 'speaking';

export interface PipelineEvent {
	type: string;
	data?: any;
	timestamp?: string;
}

/** One tool call, as `intent-tool-start` / `intent-tool-end` report it. */
export interface ToolCallEvent {
	name: string;
	arguments?: Record<string, unknown>;
	/** Which tool round, and this call's place in it. */
	round?: number;
	index?: number;
	total?: number;
	/** Only on the end event. False for a tool that answered `status: error`. */
	ok?: boolean;
	status?: string | null;
	error?: string | null;
	duration_ms?: number;
}

export interface PipelineCallbacks {
	/** Coarse state for the orb: idle / listening / thinking / speaking. */
	onState?: (state: PipelineState) => void;
	/** Final transcript from stt-end. */
	onTranscript?: (text: string) => void;
	/** Streaming LLM delta from intent-progress (data.chat_log_delta.content). */
	onDelta?: (delta: string) => void;
	/** Full response text from intent-end. */
	onResponse?: (text: string) => void;
	/**
	 * The remembered notes this turn was given, from `intent-end`.
	 *
	 * "Why did it say that?" answered with the entries the model READ. Asking
	 * the model instead produces a plausible account of notes it may never
	 * have seen.
	 */
	onMemoryUsed?: (notes: { id: string; text: string }[]) => void;
	/**
	 * A tool call starting. Fired BEFORE the call runs, so a tool that takes
	 * nine seconds is visible for nine seconds rather than reported once it is
	 * over.
	 */
	onToolStart?: (call: ToolCallEvent) => void;
	/** The same call finishing, with `ok`, `error` and how long it took. */
	onToolEnd?: (call: ToolCallEvent) => void;
	/**
	 * A slice of the model's reasoning.
	 *
	 * Never part of `onDelta`: that is the text the TTS speaks and the HUD
	 * renders as the reply, and a model's deliberation is neither. Coalesced
	 * server-side into paragraphs, so this fires a handful of times per turn
	 * rather than once per token.
	 */
	onThinking?: (delta: string) => void;

	/**
	 * The model wrote a tool call out as text instead of making one.
	 *
	 * jarvis-core noticed and is asking it to make the call properly, which
	 * costs one extra round. Worth surfacing: without it the turn simply takes
	 * longer for no visible reason, and the failure it corrects — a confident
	 * "I've started that" over work that was never dispatched — is the kind a
	 * user only discovers by asking for an update.
	 *
	 * Fires at most once per turn.
	 */
	onToolNarrated?: (tool: string) => void;
	/** TTS media path from tts-end (data.tts_output.url). */
	onTtsUrl?: (url: string) => void;
	/** Pipeline error event (data.code, data.message). */
	onError?: (code: string, message: string) => void;
	/** run-start received: binary handler id is known, audio may be streamed. */
	onReady?: (sttBinaryHandlerId: number) => void;
	/** run-end received. */
	onRunEnd?: () => void;
	/** Every raw pipeline event (for latency instrumentation / logging). */
	onEvent?: (event: PipelineEvent) => void;
}

export type SendFn = (data: string | Uint8Array) => void;

/**
 * Frame one chunk of 16 kHz mono PCM for the assist pipeline:
 * 1 prefix byte (stt_binary_handler_id) + Int16 little-endian samples.
 */
export function frameAudio(handlerId: number, pcm: Int16Array): Uint8Array {
	const out = new Uint8Array(1 + pcm.length * 2);
	out[0] = handlerId & 0xff;
	const view = new DataView(out.buffer);
	for (let i = 0; i < pcm.length; i++) {
		view.setInt16(1 + i * 2, pcm[i], true);
	}
	return out;
}

/** End-of-audio marker: a single-byte frame containing just the handler id. */
export function endFrame(handlerId: number): Uint8Array {
	return new Uint8Array([handlerId & 0xff]);
}

/**
 * One tool event's payload, typed and defaulted.
 *
 * An older jarvis-core does not send these at all, and nothing here may assume
 * a field is present: `name` is the only one a row genuinely needs, and a
 * missing `total` renders as "1 of 1" rather than "1 of undefined".
 */
export function toolCall(data: any): ToolCallEvent {
	const raw = (data ?? {}) as Record<string, any>;
	return {
		name: String(raw.name ?? 'tool'),
		arguments:
			raw.arguments && typeof raw.arguments === 'object' ? raw.arguments : {},
		round: Number(raw.round ?? 0),
		index: Number(raw.index ?? 0),
		total: Number(raw.total ?? 1),
		// `ok` is only meaningful on the end event, and `undefined` there means
		// "still running" — which is why this is not defaulted to true.
		ok: typeof raw.ok === 'boolean' ? raw.ok : undefined,
		status: raw.status ?? null,
		error: raw.error ?? null,
		duration_ms: Number(raw.duration_ms ?? 0)
	};
}

interface Pending {
	resolve: (result: any) => void;
	reject: (err: Error) => void;
}

export interface RunOptions {
	pipeline?: string | null;
	conversationId?: string | null;
	sampleRate?: number;
	/**
	 * Whether the reply should also be spoken. Text runs only.
	 *
	 * A question asked out loud is answered out loud; a question typed into a
	 * chat window usually should not be, or a console left open in a bedroom
	 * reads its replies to the room. The caller knows which it is, so the
	 * caller decides — see `startTextRun`.
	 */
	speak?: boolean;
}

export class PipelineClient {
	private nextId = 1;
	private pending = new Map<number, Pending>();
	private runId: number | null = null;

	/** stt binary handler id from run-start; null until the run is ready. */
	sttBinaryHandlerId: number | null = null;
	/** Kept across runs for conversation continuity (from intent-end). */
	conversationId: string | null = null;
	state: PipelineState = 'idle';

	private send: SendFn;
	private cb: PipelineCallbacks;

	// Note: no TS "parameter properties" here — this file is also executed
	// directly by Node's type-stripping loader (tests/web/smoke.test.mjs),
	// which only supports erasable TypeScript syntax.
	constructor(send: SendFn, cb: PipelineCallbacks = {}) {
		this.send = send;
		this.cb = cb;
	}

	/** Feed every incoming text frame from the websocket here. */
	handleMessage(raw: string | Record<string, any>): void {
		let msg: any;
		if (typeof raw === 'string') {
			try {
				msg = JSON.parse(raw);
			} catch {
				return;
			}
		} else {
			msg = raw;
		}
		if (msg.type === 'result') {
			const p = this.pending.get(msg.id);
			if (p) {
				this.pending.delete(msg.id);
				if (msg.success) p.resolve(msg.result);
				else p.reject(new Error(msg.error?.message ?? 'command failed'));
			}
			return;
		}
		if (msg.type === 'event' && msg.id === this.runId && msg.event) {
			this.handleEvent(msg.event as PipelineEvent);
		}
	}

	/** Send a command and await its result message. */
	command(payload: Record<string, any>): Promise<any> {
		const id = this.nextId++;
		const promise = new Promise<any>((resolve, reject) => {
			this.pending.set(id, { resolve, reject });
		});
		this.send(JSON.stringify({ id, ...payload }));
		return promise;
	}

	async listPipelines(): Promise<{ pipelines: any[]; preferred_pipeline: string | null }> {
		return this.command({ type: 'assist_pipeline/pipeline/list' });
	}

	/**
	 * Resolve a pipeline id by name; falls back to the preferred pipeline
	 * when no pipeline with that name exists.
	 */
	async resolvePipelineId(name: string): Promise<string | null> {
		const result = await this.listPipelines();
		const match = result.pipelines?.find((p: any) => p.name === name);
		return match?.id ?? result.preferred_pipeline ?? null;
	}

	/** Start an stt→tts pipeline run. Audio may be streamed after onReady. */
	startRun(opts: RunOptions = {}): number {
		const id = this.nextId++;
		this.runId = id;
		this.sttBinaryHandlerId = null;
		const msg: Record<string, any> = {
			id,
			type: 'assist_pipeline/run',
			start_stage: 'stt',
			end_stage: 'tts',
			input: { sample_rate: opts.sampleRate ?? 16000 },
			conversation_id: opts.conversationId !== undefined ? opts.conversationId : this.conversationId
		};
		if (opts.pipeline) msg.pipeline = opts.pipeline;
		this.send(JSON.stringify(msg));
		return id;
	}

	/**
	 * Start a run from TYPED text: the same pipeline, entered one stage later.
	 *
	 * `start_stage: 'intent'` is how the backend is told there is no audio to
	 * transcribe — the run goes straight to the conversation agent and still ends
	 * at tts, so a typed question is answered out loud exactly like a spoken one.
	 * A run started this way has no `stt_binary_handler_id`, so `sendAudio` and
	 * `endAudio` stay no-ops for its whole life and there is no half-open audio
	 * stream to close.
	 *
	 * This exists because the HUD's microphone is not always available — a denied
	 * permission, a machine with no microphone — and without it the answer to
	 * "the browser said no" was that Jarvis could not be spoken to at all.
	 */
	startTextRun(text: string, opts: RunOptions = {}): number {
		const id = this.nextId++;
		this.runId = id;
		this.sttBinaryHandlerId = null;
		const msg: Record<string, any> = {
			id,
			type: 'assist_pipeline/run',
			start_stage: 'intent',
			// Stopping at `intent` skips synthesis entirely rather than
			// synthesising and discarding it — a chat message that is not going
			// to be spoken should not spend a Piper round trip on being spoken.
			end_stage: opts.speak === false ? 'intent' : 'tts',
			input: { text },
			conversation_id: opts.conversationId !== undefined ? opts.conversationId : this.conversationId
		};
		if (opts.pipeline) msg.pipeline = opts.pipeline;
		this.send(JSON.stringify(msg));
		return id;
	}

	/** Stream one chunk of 16 kHz Int16 PCM (no-op until run-start arrived). */
	sendAudio(pcm: Int16Array): void {
		if (this.sttBinaryHandlerId === null) return;
		this.send(frameAudio(this.sttBinaryHandlerId, pcm));
	}

	/** Signal end of audio with the single-byte handler-id frame. */
	endAudio(): void {
		if (this.sttBinaryHandlerId === null) return;
		this.send(endFrame(this.sttBinaryHandlerId));
	}

	private setState(s: PipelineState): void {
		if (this.state !== s) {
			this.state = s;
			this.cb.onState?.(s);
		}
	}

	private handleEvent(ev: PipelineEvent): void {
		this.cb.onEvent?.(ev);
		switch (ev.type) {
			case 'run-start': {
				const handler = ev.data?.runner_data?.stt_binary_handler_id;
				if (typeof handler === 'number') {
					this.sttBinaryHandlerId = handler;
					this.cb.onReady?.(handler);
				}
				this.setState('listening');
				break;
			}
			case 'stt-start':
			case 'stt-vad-start':
			case 'stt-vad-end':
			case 'intent-start':
			case 'tts-start':
				break;
			case 'stt-end': {
				const text = ev.data?.stt_output?.text ?? '';
				this.cb.onTranscript?.(text);
				this.setState('thinking');
				break;
			}
			case 'intent-progress': {
				const delta = ev.data?.chat_log_delta?.content;
				if (typeof delta === 'string' && delta.length > 0) this.cb.onDelta?.(delta);
				break;
			}
			case 'intent-tool-start': {
				this.cb.onToolStart?.(toolCall(ev.data));
				break;
			}
			case 'intent-tool-end': {
				this.cb.onToolEnd?.(toolCall(ev.data));
				break;
			}
			case 'intent-tool-narrated': {
				const tool = ev.data?.tool;
				if (typeof tool === 'string' && tool) this.cb.onToolNarrated?.(tool);
				break;
			}
			case 'intent-thinking': {
				const delta = ev.data?.delta;
				if (typeof delta === 'string' && delta.length > 0) this.cb.onThinking?.(delta);
				break;
			}
			case 'intent-end': {
				const output = ev.data?.intent_output;
				const speech = output?.response?.speech?.plain?.speech ?? '';
				if (output?.conversation_id) this.conversationId = output.conversation_id;
				const used = output?.response?.data?.memory_used;
				if (Array.isArray(used) && used.length) this.cb.onMemoryUsed?.(used);
				this.cb.onResponse?.(speech);
				break;
			}
			case 'tts-end': {
				const url = ev.data?.tts_output?.url;
				if (typeof url === 'string') this.cb.onTtsUrl?.(url);
				this.setState('speaking');
				break;
			}
			case 'run-end': {
				this.runId = null;
				this.sttBinaryHandlerId = null;
				this.cb.onRunEnd?.();
				break;
			}
			case 'error': {
				this.runId = null;
				this.sttBinaryHandlerId = null;
				this.cb.onError?.(ev.data?.code ?? 'unknown', ev.data?.message ?? 'pipeline error');
				this.setState('idle');
				break;
			}
		}
	}
}
