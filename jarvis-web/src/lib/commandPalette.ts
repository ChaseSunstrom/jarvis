// Command palette logic, with no DOM in sight.
//
// `CommandPalette.svelte` is a thin shell: it owns focus and keystrokes, and
// asks this module what the list is, which row is selected, and what pressing
// Enter should do. Everything below is a pure function, so the parts that are
// actually easy to get wrong — the ranking, the wrap-around, which entities can
// be toggled — are unit-tested rather than eyeballed.

import { areaForEntity, areaKey, domainOf, friendlyName, isOn } from './jarvisClient';
import type {
	AreaEntry,
	DeviceRegistryEntry,
	EntityRegistryEntry,
	EntityState
} from './jarvisClient';

export type PaletteKind = 'page' | 'entity' | 'automation' | 'area';

export interface PaletteToggle {
	domain: string;
	service: 'turn_on' | 'turn_off';
}

export interface PaletteItem {
	/** Stable, unique, and safe to put in a `data-testid`. */
	id: string;
	kind: PaletteKind;
	/** What the user reads. */
	label: string;
	/** Second line: entity id, area name, the route. */
	detail: string;
	/** Where "jump to this" goes. */
	href: string;
	/** Present on entity rows; what Enter would call. */
	toggle?: PaletteToggle;
	entityId?: string;
	/** Extra text that matches but is not displayed (aliases, area names). */
	keywords?: string;
}

/** Domains whose entities the palette is willing to flip from a one-line search. */
export const TOGGLE_DOMAINS = new Set([
	'light',
	'switch',
	'fan',
	'siren',
	'input_boolean',
	'automation',
	'humidifier'
]);

/** The console's own routes, always present so the palette works while offline. */
export const PAGE_ITEMS: readonly PaletteItem[] = [
	{ id: 'page:/', kind: 'page', label: 'Voice HUD', detail: '/', href: '/', keywords: 'talk mic orb' },
	{ id: 'page:/devices', kind: 'page', label: 'Devices', detail: '/devices', href: '/devices', keywords: 'entities states' },
	{ id: 'page:/areas', kind: 'page', label: 'Areas', detail: '/areas', href: '/areas', keywords: 'rooms zones' },
	{ id: 'page:/automations', kind: 'page', label: 'Automations', detail: '/automations', href: '/automations', keywords: 'routines' },
	{ id: 'page:/tools', kind: 'page', label: 'Tools', detail: '/tools', href: '/tools', keywords: 'llm catalogue exposure' },
	{ id: 'page:/tasks', kind: 'page', label: 'Tasks', detail: '/tasks', href: '/tasks', keywords: 'jobs progress research background scheduled' },
	{ id: 'page:/settings', kind: 'page', label: 'Settings', detail: '/settings', href: '/settings', keywords: 'backend events log' }
];

/** The toggle Enter would perform, or undefined when the entity is not flippable. */
export function toggleFor(state: EntityState): PaletteToggle | undefined {
	const domain = domainOf(state.entity_id);
	if (!TOGGLE_DOMAINS.has(domain)) return undefined;
	if (state.state === 'unavailable' || state.state === 'unknown') return undefined;
	return { domain, service: isOn(state) ? 'turn_off' : 'turn_on' };
}

export interface PaletteSource {
	states?: EntityState[];
	areas?: AreaEntry[];
	entries?: EntityRegistryEntry[];
	devices?: DeviceRegistryEntry[];
}

/**
 * Everything the palette can jump to: the routes, every area, and every entity
 * (automations split out so they carry the right verb and land on their page).
 */
export function buildPaletteItems(source: PaletteSource): PaletteItem[] {
	const entries = source.entries ?? [];
	const areas = source.areas ?? [];
	const devices = source.devices ?? [];
	const entryMap = new Map(entries.map((e) => [e.entity_id, e]));
	const deviceMap = new Map(devices.map((d) => [d.id, d]));
	const areaNames = new Map(areas.map((a) => [areaKey(a), a.name]));

	const items: PaletteItem[] = [...PAGE_ITEMS];

	for (const area of areas) {
		const id = areaKey(area);
		items.push({
			id: `area:${id}`,
			kind: 'area',
			label: area.name,
			detail: `area · ${id}`,
			href: `/areas?focus=${encodeURIComponent(id)}`,
			keywords: (area.aliases ?? []).join(' ')
		});
	}

	for (const state of source.states ?? []) {
		const entry = entryMap.get(state.entity_id);
		if (entry?.disabled) continue;
		const domain = domainOf(state.entity_id);
		const areaId = areaForEntity(state.entity_id, entryMap, deviceMap);
		const areaName = areaId ? (areaNames.get(areaId) ?? areaId) : '';
		const label = friendlyName(state, entry);
		const automation = domain === 'automation';
		items.push({
			id: `${automation ? 'automation' : 'entity'}:${state.entity_id}`,
			kind: automation ? 'automation' : 'entity',
			label,
			detail: areaName ? `${state.entity_id} · ${areaName}` : state.entity_id,
			href: `${automation ? '/automations' : '/devices'}?focus=${encodeURIComponent(state.entity_id)}`,
			toggle: toggleFor(state),
			entityId: state.entity_id,
			keywords: [state.state, areaName, ...(entry?.aliases ?? [])].filter(Boolean).join(' ')
		});
	}

	return items;
}

/**
 * Subsequence match with a score, higher is better; null means "no match".
 *
 * The scoring only has to be *stable and sensible*, not clever: an exact hit
 * beats a prefix, a prefix beats a word boundary, a word boundary beats a
 * scattered subsequence, and a shorter haystack breaks ties. That ordering is
 * what the tests pin, so a future rewrite of the internals cannot quietly
 * reshuffle what the first row is.
 */
export function fuzzyScore(haystack: string, needle: string): number | null {
	const hay = haystack.toLowerCase();
	const pin = needle.toLowerCase().trim();
	if (!pin) return 0;
	if (hay === pin) return 1000 - hay.length;

	const at = hay.indexOf(pin);
	if (at === 0) return 800 - hay.length;
	if (at > 0) {
		const boundary = at === 0 || /[\s._\-/:]/.test(hay[at - 1]);
		return (boundary ? 640 : 520) - at - hay.length * 0.1;
	}

	// Scattered subsequence: every needle character in order, anywhere.
	let hi = 0;
	let runs = 0;
	let lastMatch = -2;
	for (const ch of pin) {
		const found = hay.indexOf(ch, hi);
		if (found < 0) return null;
		if (found !== lastMatch + 1) runs += 1;
		lastMatch = found;
		hi = found + 1;
	}
	return 300 - runs * 8 - hay.length * 0.1;
}

/** The text a query is matched against for one item. */
export function haystackFor(item: PaletteItem): string[] {
	return [item.label, item.detail, item.keywords ?? ''].filter(Boolean);
}

/**
 * Rank items against a query. An empty query keeps source order (pages first,
 * then areas, then entities), which is a usable "what is here?" list.
 */
export function filterPalette(items: PaletteItem[], query: string, limit = 40): PaletteItem[] {
	const pin = query.trim();
	if (!pin) return items.slice(0, limit);

	const scored: { item: PaletteItem; score: number; order: number }[] = [];
	items.forEach((item, order) => {
		let best: number | null = null;
		for (const field of haystackFor(item)) {
			const score = fuzzyScore(field, pin);
			if (score === null) continue;
			// Later fields are weaker signals than the label.
			const weighted = field === item.label ? score : score - 60;
			if (best === null || weighted > best) best = weighted;
		}
		if (best !== null) scored.push({ item, score: best, order });
	});

	scored.sort((a, b) => b.score - a.score || a.order - b.order);
	return scored.slice(0, limit).map((s) => s.item);
}

/** Wrap-around cursor movement. An empty list stays at 0. */
export function moveIndex(index: number, delta: number, length: number): number {
	if (length <= 0) return 0;
	const next = (index + delta) % length;
	return next < 0 ? next + length : next;
}

/** Keep a selection in range after the list changed under it. */
export function clampIndex(index: number, length: number): number {
	if (length <= 0) return 0;
	if (!Number.isFinite(index) || index < 0) return 0;
	return Math.min(Math.floor(index), length - 1);
}

export type PaletteAction =
	| { type: 'navigate'; href: string }
	| { type: 'call'; entityId: string; domain: string; service: string; label: string }
	| { type: 'none' };

/**
 * What activating `item` should do.
 *
 * Enter on a flippable entity toggles it — that is the whole point of typing
 * "lab lights" into a box. Everything else jumps to the page that owns it, and
 * `alternate` (Shift+Enter) forces the jump even for a toggleable entity.
 */
export function actionFor(item: PaletteItem | undefined, alternate = false): PaletteAction {
	if (!item) return { type: 'none' };
	if (!alternate && item.toggle && item.entityId) {
		return {
			type: 'call',
			entityId: item.entityId,
			domain: item.toggle.domain,
			service: item.toggle.service,
			label: item.label
		};
	}
	return { type: 'navigate', href: item.href };
}

/** The hint shown next to the selected row. */
export function hintFor(item: PaletteItem | undefined): string {
	if (!item) return '';
	if (item.toggle) return `⏎ ${item.toggle.service === 'turn_on' ? 'turn on' : 'turn off'} · ⇧⏎ open`;
	return '⏎ open';
}
