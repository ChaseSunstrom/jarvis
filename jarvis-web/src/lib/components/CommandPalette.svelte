<script lang="ts">
	// Ctrl/Cmd-K: type a few letters, hit Enter.
	//
	// All of the logic worth getting right — ranking, wrap-around, which
	// entities can be flipped and what Enter therefore means — lives in
	// `$lib/commandPalette` as pure functions. This component owns focus,
	// keystrokes and the ARIA combobox wiring, and nothing else.
	import { tick } from 'svelte';
	import { goto } from '$app/navigation';
	import {
		actionFor,
		buildPaletteItems,
		clampIndex,
		filterPalette,
		hintFor,
		moveIndex,
		type PaletteItem,
		type PaletteSource
	} from '$lib/commandPalette';
	import { serviceFailureText, serviceSuccessText, toasts } from '$lib/toast';
	import { describeError } from '$lib/connection';

	interface Props {
		open: boolean;
		source: PaletteSource;
		/** Issues a service call on the console's own socket. */
		call: (domain: string, service: string, data: Record<string, any>) => Promise<unknown>;
		onClose: () => void;
	}
	let { open, source, call, onClose }: Props = $props();

	let query = $state('');
	let index = $state(0);
	let input = $state<HTMLInputElement | null>(null);
	let listEl = $state<HTMLUListElement | null>(null);

	let items = $derived(buildPaletteItems(source));
	let visible = $derived(filterPalette(items, query));
	let selected = $derived(visible[clampIndex(index, visible.length)]);

	// Reopening starts clean; a list that shrank under the cursor pulls it back
	// into range rather than leaving Enter pointing at nothing.
	$effect(() => {
		if (open) {
			query = '';
			index = 0;
			void tick().then(() => input?.focus());
		}
	});
	$effect(() => {
		index = clampIndex(index, visible.length);
	});

	function scrollSelectedIntoView(): void {
		const el = listEl?.querySelector('[aria-selected="true"]');
		(el as HTMLElement | null)?.scrollIntoView({ block: 'nearest' });
	}

	function move(delta: number): void {
		index = moveIndex(index, delta, visible.length);
		void tick().then(scrollSelectedIntoView);
	}

	async function activate(item: PaletteItem | undefined, alternate = false): Promise<void> {
		const action = actionFor(item, alternate);
		if (action.type === 'none') return;
		onClose();
		if (action.type === 'navigate') {
			await goto(action.href);
			return;
		}
		try {
			await call(action.domain, action.service, { entity_id: action.entityId });
			toasts.success(serviceSuccessText(action.service, action.label), action.entityId);
		} catch (err) {
			toasts.error(serviceFailureText(action.service, action.label), describeError(err));
		}
	}

	function onKeyDown(e: KeyboardEvent): void {
		switch (e.key) {
			case 'Escape':
				e.preventDefault();
				onClose();
				return;
			case 'ArrowDown':
				e.preventDefault();
				move(1);
				return;
			case 'ArrowUp':
				e.preventDefault();
				move(-1);
				return;
			case 'Home':
				e.preventDefault();
				index = 0;
				return;
			case 'End':
				e.preventDefault();
				index = Math.max(0, visible.length - 1);
				return;
			case 'Enter':
				e.preventDefault();
				void activate(selected, e.shiftKey);
				return;
			case 'Tab':
				// Nothing behind the palette is reachable while it is open.
				e.preventDefault();
				move(e.shiftKey ? -1 : 1);
		}
	}
</script>

{#if open}
	<!--
		The scrim is a mouse affordance, not a control: Escape closes the palette
		from the keyboard, and the input keeps focus the whole time it is open.
	-->
	<!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
	<div class="jv-palette-scrim" data-testid="palette-scrim" onclick={onClose}></div>
	<div
		class="jv-palette"
		data-testid="palette"
		role="dialog"
		aria-modal="true"
		aria-label="Command palette"
	>
		<div class="jv-palette-head">
			<span class="jv-palette-prompt" aria-hidden="true">&gt;</span>
			<!--
				`aria-expanded` and `aria-controls` track the list rather than
				asserting it. They were hardcoded to true and to
				"jv-palette-list", and that `<ul>` only exists while something
				matches — so a query that matched nothing announced an expanded
				listbox and pointed at an id that was not in the document, which
				is a combobox a screen reader cannot navigate and cannot explain.
			-->
			<!-- svelte-ignore a11y_autofocus -->
			<input
				bind:this={input}
				bind:value={query}
				class="jv-palette-input"
				data-testid="palette-input"
				type="text"
				role="combobox"
				aria-expanded={visible.length > 0}
				aria-controls={visible.length ? 'jv-palette-list' : undefined}
				aria-activedescendant={visible.length ? `jv-palette-opt-${clampIndex(index, visible.length)}` : undefined}
				aria-label="Search entities, areas, automations and pages"
				autocomplete="off"
				spellcheck="false"
				placeholder="jump to an entity, area, automation or page…"
				onkeydown={onKeyDown}
			/>
			<span class="jv-palette-state" data-testid="palette-count">{visible.length}</span>
		</div>

		{#if visible.length}
			<ul
				bind:this={listEl}
				id="jv-palette-list"
				class="jv-palette-list"
				role="listbox"
				aria-label="Results"
			>
				{#each visible as item, i (item.id)}
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<li
						id="jv-palette-opt-{i}"
						class="jv-palette-item"
						role="option"
						aria-selected={i === clampIndex(index, visible.length)}
						data-testid="palette-item-{item.id}"
						onmousemove={() => (index = i)}
						onmousedown={(e) => {
							e.preventDefault();
							void activate(item, e.shiftKey);
						}}
					>
						<span class="jv-palette-kind">{item.kind}</span>
						<span class="jv-palette-label">
							<b>{item.label}</b>
							<span>{item.detail}</span>
						</span>
						{#if item.toggle}
							<span class="jv-palette-state"
								>{item.toggle.service === 'turn_on' ? 'off' : 'on'}</span
							>
						{/if}
					</li>
				{/each}
			</ul>
		{:else}
			<p class="jv-palette-none" data-testid="palette-none">
				Nothing matches “{query}”.
			</p>
		{/if}

		<div class="jv-palette-foot">
			<span data-testid="palette-hint">{hintFor(selected)}</span>
			<span>↑↓ move · esc close</span>
		</div>
	</div>
{/if}
