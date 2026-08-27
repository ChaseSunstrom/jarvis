<!--
@component
MODELS: what the model servers actually serve (M54).

The settings page used to offer "Model" as a dropdown of `house` and
`house-fast`, which are the gateway's names for things and not the things.
This panel lists the models — the ids llama-swap, vLLM, llama.cpp or Ollama
answer with, and the embedder and reranker in their own containers — one
hairline row each: the name, `family · size · quant` in mono, the role as a
tag, a lit dot when it is loaded right now, and "used for …" in plain words.
Under the list, one choice per role — chat, fast, vision — that writes the
same setting the raw row would (`llm.model`, `llm.fast_model`,
`vision.model`) through the same API, so choosing here and choosing there
cannot disagree.

What the panel says is what `jarvis/llm/models` found. A size that was read
off the id rather than reported by the server says "as named by the server";
a configured model no server lists is a row that says so; a server that did
not answer is named at the foot with why.

```svelte
<Models conn={link.conn} status={link.status} onsaved={(r) => store.absorb(r)} />
```
-->
<script lang="ts">
	import type { Connection, ConnectionStatus } from '$lib/connection';
	import { describeError } from '$lib/connection';
	import type { ModelRow, ModelsPayload, SettingResult } from '$lib/jarvisClient';
	import { toasts } from '$lib/toast';
	import { staggerStyle } from '$lib/motion';
	import { Panel, Pill, ScreenState, Select } from '$lib/ui';

	interface Props {
		conn: Connection | null;
		status: ConnectionStatus;
		/** A role choice wrote a setting: the section's rows want the new list. */
		onsaved?: (result: SettingResult) => void;
	}
	let { conn, status, onsaved }: Props = $props();

	let payload = $state<ModelsPayload | null>(null);
	let error = $state('');
	let loading = $state(false);
	let busyRole = $state('');

	/**
	 * The panel's own four states, on the section's link.
	 *
	 * Offline is the link's word, not this panel's: when the socket is down the
	 * rows stay (they are the last thing the server said) under the section's
	 * banner. Loading is until the first answer; error is that answer failing;
	 * empty is a server that answered with nothing.
	 */
	const panel = $derived<'loading' | 'ready' | 'empty' | 'error' | 'offline'>(
		status === 'closed' || status === 'error'
			? 'offline'
			: error
				? 'error'
				: !payload
					? 'loading'
					: payload.models.length === 0
						? 'empty'
						: 'ready'
	);

	export async function load(): Promise<void> {
		if (!conn) return;
		loading = true;
		error = '';
		try {
			payload = await conn.client.listModels();
		} catch (e) {
			error = describeError(e);
		} finally {
			loading = false;
		}
	}

	// Load when the connection arrives, and again after a redial: the section
	// hands in a new `conn` each time it dials, and each one is a fresh socket
	// that has answered nothing yet.
	$effect(() => {
		if (conn) void load();
	});

	// Live (M99): a voice `change_setting` on a model role moved the row and
	// left this pill on the old model until a reload — the section listened
	// for nothing. One subscription per connection, dropped with it.
	$effect(() => {
		const connection = conn;
		if (!connection) return;
		let sub: { unsubscribe: () => Promise<void> } | null = null;
		let gone = false;
		void connection.client
			.subscribeEvents((event) => {
				const key = String((event.data as { key?: string } | undefined)?.key ?? '');
				if (key.startsWith('llm.') || key.startsWith('vision.') || key.startsWith('voice.')) void load();
			}, 'jarvis_setting_changed')
			.then((s) => {
				if (gone) void s.unsubscribe();
				else sub = s;
			})
			.catch(() => {
				// An older server without the event still lists on demand.
			});
		return () => {
			gone = true;
			void sub?.unsubscribe();
		};
	});

	const models = $derived(payload?.models ?? []);

	/** `family · size · quant`, the parts the server (or the id) gave. */
	function meta(model: ModelRow): string {
		return [model.family, model.parameters, model.quant].filter(Boolean).join(' · ');
	}

	/** "used for conversation, research and coding" — plain words, Oxford-free. */
	function usedFor(model: ModelRow): string {
		const jobs = model.in_use_for;
		if (!jobs.length) return model.missing ? 'configured, not served' : 'not used by anything';
		if (jobs.length === 1) return `used for ${jobs[0]}`;
		return `used for ${jobs.slice(0, -1).join(', ')} and ${jobs[jobs.length - 1]}`;
	}

	function loadedWord(model: ModelRow): string {
		return model.loaded === true ? 'loaded' : model.loaded === false ? 'not loaded' : 'unknown';
	}

	function roleTone(role: ModelRow['role']): 'neutral' | 'live' | 'ok' | 'warn' | 'danger' {
		return role === 'chat' ? 'live' : 'neutral';
	}

	/**
	 * What a chat or fast choice may name: everything `LLM_URL` can reach that
	 * is not an embedder or a reranker. A vision model can hold a conversation;
	 * a cross-encoder cannot.
	 */
	const chatOptions = $derived.by(() => {
		const rows = models.filter((m) => m.choice && m.role !== 'embeddings' && m.role !== 'rerank');
		return rows.map((m) => ({
			value: m.choice as string,
			label: m.choice && m.choice !== m.id ? `${m.name} — as ${m.choice}` : m.name === m.id ? m.id : `${m.name} — ${m.id}`
		}));
	});

	/** The current value kept even when the list does not carry it, the way the raw row does. */
	function withCurrent(options: { value: string; label: string }[], current: string, none = ''): { value: string; label: string }[] {
		const out = [...options];
		if (none) out.unshift({ value: '', label: none });
		if (current && !out.some((o) => o.value === current)) out.push({ value: current, label: `${current} — not listed by any server` });
		return out;
	}

	const chatChoices = $derived(withCurrent(chatOptions, payload?.roles.chat.value ?? ''));
	const fastChoices = $derived(withCurrent(chatOptions, payload?.roles.fast.value ?? '', 'same as chat'));
	const visionChoices = $derived(
		withCurrent(
			models.filter((m) => m.role === 'vision' && !m.missing).map((m) => ({ value: m.id, label: m.name === m.id ? m.id : `${m.name} — ${m.id}` })),
			payload?.roles.vision.value ?? ''
		)
	);
	const visionConfigured = $derived(payload?.roles.vision.configured ?? false);
	const visionServed = $derived(payload?.roles.vision.served ?? false);
	const visionCameras = $derived(payload?.roles.vision.cameras ?? 0);
	/**
	 * What to say beside the vision chooser, in the order the fixes go: no
	 * block, a model no server lists, no camera, all set. The operator read
	 * "cameras are not configured" when the block was there and the model was
	 * not served, and asked why the model was not set — it was; the wrong
	 * sentence hid that.
	 */
	const visionWhy = $derived.by(() => {
		if (!visionConfigured) return 'No vision: block in configuration.yaml yet — add one, naming the model and a camera.';
		const value = payload?.roles.vision.value ?? '';
		if (!visionServed) {
			const served = payload?.roles.vision.served_vision ?? [];
			return served.length
				? `vision.model is "${value}", which no server lists; ${served.join(', ')} ${served.length === 1 ? 'is' : 'are'} served — choose one, or load a model under "${value}".`
				: `vision.model is "${value}", and the model server serves no vision model at all — load a vision model under that name (llama-swap: a GGUF VLM as "${value}") and it appears here.`;
		}
		if (!visionCameras) return 'Served. No camera to point it at yet: add one under vision: cameras: in configuration.yaml.';
		return 'Looks at a camera frame when asked. Named as the vision server names it.';
	});

	async function choose(role: 'chat' | 'fast' | 'vision', value: string): Promise<void> {
		if (!conn || !payload) return;
		const setting = payload.roles[role].setting;
		if (value === payload.roles[role].value) return;
		busyRole = role;
		try {
			const result = await conn.client.setSetting(setting, value);
			onsaved?.(result);
			toasts.success(
				`${role === 'chat' ? 'Chat' : role === 'fast' ? 'Fast' : 'Vision'} model · ${value || 'same as chat'}`,
				result.restart_required ? 'saved; restart to apply' : 'in effect now'
			);
			await load();
		} catch (e) {
			toasts.error(`Could not set the ${role} model`, describeError(e));
			error = '';
			// The select shows the old value again: the server refused, so the
			// old value is still the truth.
			await load();
		} finally {
			busyRole = '';
		}
	}

	const failedServers = $derived((payload?.servers ?? []).filter((s) => !s.ok));
	const summary = $derived.by(() => {
		if (!payload) return '…';
		const loaded = models.filter((m) => m.loaded === true).length;
		return `${models.length} served · ${loaded} loaded${payload.gateway ? ' · via the gateway' : ''}`;
	});
</script>

<Panel title="Models" meta={summary} live={panel === 'ready'} testid="models">
	{#snippet children()}
		<ScreenState
			status={panel}
			rows={5}
			emptyTitle="The model server lists nothing"
			emptyBody="LLM_URL answered, and named no models. Start one in llama-swap, or point LLM_URL at the server that has them."
			errorTitle="Could not read the model servers"
			errorDetail={error}
			onretry={load}
			offlineBody="The link is down; this list is the last thing the server said. The section's RECONNECT above re-dials and re-reads it."
			busy={loading}
			errorTestid="models-error"
			emptyTestid="models-empty"
			offlineTestid="models-offline"
		>
			{#snippet children()}
				<ul class="rows" data-testid="models-list">
					{#each models as model, i (model.id)}
						<li
							class="model jv-stagger"
							class:missing={model.missing}
							style={staggerStyle(i)}
							data-testid="model-{model.id}"
							data-loaded={loadedWord(model)}
							data-role={model.role}
						>
							<span class="dot" class:lit={model.loaded === true} class:off={model.loaded === false} aria-hidden="true"></span>
							<span class="sr" data-testid="model-loaded-{model.id}">{loadedWord(model)}</span>
							<div class="what">
								<b data-testid="model-name-{model.id}">{model.name}</b>
								<span class="meta" data-testid="model-meta-{model.id}">
									{#if meta(model)}<span>{meta(model)}</span>{/if}
									{#if model.name !== model.id}<span class="id">{model.id}</span>{/if}
									{#if model.context}<span>{Math.round(model.context / 1024)}k context</span>{/if}
								</span>
								<span class="use" data-testid="model-use-{model.id}">
									{usedFor(model)}{#if model.aliases.length}<span class="alias"> · as {model.aliases.join(', ')} at the gateway</span>{/if}
								</span>
								{#if model.note}<span class="note" data-testid="model-note-{model.id}">{model.note}</span>{/if}
							</div>
							<span class="tags">
								{#if model.missing}
									<Pill tone="danger" testid="model-missing-{model.id}">not served</Pill>
								{/if}
								<Pill tone={roleTone(model.role)} testid="model-role-{model.id}">{model.role}</Pill>
							</span>
						</li>
					{/each}
				</ul>

				<!-- The choices. One per role, each writing its setting through the
				     same API the raw rows use; a value is what LLM_URL names it,
				     shown against the model it stands for. -->
				<div class="roles" data-testid="model-roles">
					<div class="role">
						<div class="what">
							<b>Chat</b>
							<span class="why">Answers every conversation, and research and coding unless those name their own.</span>
						</div>
						<Select
							value={payload?.roles.chat.value ?? ''}
							testid="role-chat"
							options={chatChoices}
							disabled={busyRole !== '' || !chatChoices.length}
							onchange={(e) => choose('chat', (e.currentTarget as HTMLSelectElement).value)}
						/>
					</div>
					<div class="role">
						<div class="what">
							<b>Fast</b>
							<span class="why">
								A smaller model for the voice path.
								{#if payload?.roles.fast.source === 'gateway'}The gateway routes <code>house-fast</code> to it; nothing in Jarvis uses that route yet.{:else}Nothing routes to it yet — the fast path lands with M60.{/if}
							</span>
						</div>
						<Select
							value={payload?.roles.fast.value ?? ''}
							testid="role-fast"
							options={fastChoices}
							disabled={busyRole !== '' || !chatOptions.length}
							onchange={(e) => choose('fast', (e.currentTarget as HTMLSelectElement).value)}
						/>
					</div>
					<div class="role">
						<div class="what">
							<b>Vision</b>
							<span class="why" data-testid="role-vision-why">{visionWhy}</span>
						</div>
						<Select
							value={payload?.roles.vision.value ?? ''}
							testid="role-vision"
							options={visionChoices.length ? visionChoices : [{ value: '', label: 'no vision model is served' }]}
							disabled={busyRole !== '' || !visionConfigured || !visionChoices.length}
							onchange={(e) => choose('vision', (e.currentTarget as HTMLSelectElement).value)}
						/>
					</div>
				</div>
			{/snippet}
		</ScreenState>

		{#if failedServers.length}
			<ul class="servers" data-testid="models-servers">
				{#each failedServers as server (server.url + server.role)}
					<li>
						<Pill tone="warn">{server.role || server.kind}</Pill>
						<code>{server.url}</code>
						<span>{server.error}</span>
					</li>
				{/each}
			</ul>
		{/if}
	{/snippet}
</Panel>

<style>
	.rows {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	/* One model on a hairline: the dot, what it is, its tags. */
	.model {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: start;
		gap: var(--jv-space-2) var(--jv-space-3);
		padding: var(--jv-space-3) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.model:last-child {
		border-bottom: 0;
	}
	.model.missing .what b {
		color: var(--jv-text-dim);
	}
	/* The dot: lit when the model is resident. A ring when it is not; nothing
	   when the server cannot say — an unlit dot would claim "not loaded". */
	.dot {
		display: inline-block;
		width: var(--jv-space-2);
		height: var(--jv-space-2);
		margin-top: var(--jv-space-2);
		border-radius: 50%;
		border: 1px solid var(--jv-line-soft);
		background: transparent;
	}
	.dot.lit {
		border-color: var(--jv-accent);
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-sm);
	}
	.dot.off {
		border-color: var(--jv-line);
	}
	.sr {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}
	.what {
		display: grid;
		gap: var(--jv-space-1);
		min-width: 0;
	}
	.what b {
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
	}
	/* family · size · quant, the id, the context: data, so mono. */
	.meta {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-3);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-dim);
	}
	.meta .id {
		color: var(--jv-text-faint);
		overflow-wrap: anywhere;
	}
	.use {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text);
	}
	.alias {
		color: var(--jv-text-dim);
	}
	.note {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.tags {
		display: flex;
		gap: var(--jv-space-2);
		align-items: center;
		flex-wrap: wrap;
		justify-content: flex-end;
	}

	/* The three choices, under a hairline, each a labelled select. */
	.roles {
		display: grid;
		gap: 0;
		margin-top: var(--jv-space-3);
		border-top: 1px solid var(--jv-line-soft);
	}
	.role {
		display: grid;
		grid-template-columns: minmax(12rem, 1fr) minmax(12rem, 1.4fr);
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
		padding: var(--jv-space-3) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.role:last-child {
		border-bottom: 0;
	}
	.why {
		font-size: var(--jv-fs-xs);
		line-height: 1.5;
		color: var(--jv-text-dim);
		max-width: 48ch;
	}
	.why code,
	.servers code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text);
	}
	.servers {
		list-style: none;
		margin: var(--jv-space-3) 0 0;
		padding: var(--jv-space-3) 0 0;
		border-top: 1px solid var(--jv-line-hair);
		display: grid;
		gap: var(--jv-space-2);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.servers li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-3);
	}
	@media (max-width: 720px) {
		.model {
			grid-template-columns: auto minmax(0, 1fr);
		}
		.tags {
			grid-column: 2;
			justify-content: flex-start;
		}
		.role {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
