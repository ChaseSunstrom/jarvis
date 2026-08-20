/**
 * Skills, as the console understands it.
 *
 * ## Why this page can read a body and the model cannot
 *
 * A skill is instructions, and an installed one was written by a stranger.
 * jarvis-core will not put a body in front of the model until the skill is
 * switched on — but somebody has to READ it to decide whether to switch it
 * on, and that somebody is here, with a bearer token, on purpose.
 *
 * So `getSkill` returns the body and `open_skill` (the model's tool) refuses
 * for a disabled skill. That asymmetry is the feature, not an oversight.
 */

export type SkillSource = 'builtin' | 'authored' | 'installed' | 'broken';

export interface SkillRow {
	name: string;
	description: string;
	source: SkillSource | string;
	enabled: boolean;
	license: string;
	version: string;
	/** `owner/repo` when it was installed, else "". */
	origin: string;
	chars: number;
	/** Present only on a `broken` row: why it would not load. */
	problem?: string;
}

export interface SkillDetail extends SkillRow {
	body: string;
}

export interface SkillListing {
	skills: SkillRow[];
	/** How much of the prompt the catalogue currently costs. */
	catalogue_chars: number;
	sources: { project: string; branch: string }[];
	install_enabled: boolean;
}

/** Where a skill came from, as something to show rather than a slug. */
export function describeSource(row: SkillRow): string {
	switch (row.source) {
		case 'builtin':
			return 'ships with Jarvis';
		case 'installed':
			return row.origin ? `installed from ${row.origin}` : 'installed';
		case 'broken':
			return 'will not load';
		default:
			return 'written here';
	}
}

/**
 * Why a skill is off, when that is worth explaining.
 *
 * An installed skill being off is not a fault — it is the design, and saying
 * so is the difference between "switch it on when you have read it" and "why
 * is this broken".
 */
export function describeDisabled(row: SkillRow): string {
	if (row.enabled || row.source === 'broken') return '';
	if (row.source === 'installed') {
		return 'Installed but off. A skill is instructions — read it, then switch it on.';
	}
	return 'Off. Jarvis will not see it at all.';
}

/** True when this row can be removed. A shipped skill can only be switched off. */
export function canForget(row: SkillRow): boolean {
	return row.source !== 'builtin';
}

/**
 * What the catalogue costs, in words a person can weigh.
 *
 * The whole argument for skills is that the prompt stays small, so the number
 * belongs on the page rather than in a docstring — a household that installs
 * thirty skills should be able to see the cost climbing.
 */
export function describeCatalogue(listing: SkillListing | null): string {
	if (!listing) return '';
	const on = listing.skills.filter((s) => s.enabled).length;
	if (!on) return 'No skills are on, so Jarvis is told nothing about them.';
	const bodies = listing.skills
		.filter((s) => s.enabled)
		.reduce((total, s) => total + s.chars, 0);
	return (
		`${on} skill${on === 1 ? '' : 's'} on, costing ${listing.catalogue_chars} ` +
		`characters of every prompt. Their instructions are ${bodies} characters, ` +
		'loaded only when Jarvis opens one.'
	);
}

/** `_SEGMENT_RE` in jarvis-core's skills/install.py, character for character. */
const SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

/**
 * Whether a reference looks installable, as a sentence or "".
 *
 * A copy of `parse_reference` in jarvis-core, for the same reason as every
 * other mirror here: the server decides, and the form should not make somebody
 * wait for a round trip to be told they pasted a URL.
 */
export function whyNotReference(text: string, sources: readonly string[]): string {
	let said = text.trim();
	let branch = '';
	if (said.includes('@')) {
		const at = said.indexOf('@');
		branch = said.slice(at + 1).trim();
		said = said.slice(0, at);
	}
	said = said.trim().replace(/^\/+|\/+$/g, '');
	if (!said) return 'Which skill? Give it as owner/repo/path-to-the-skill.';
	if (/^https?:\/\//i.test(said)) {
		return 'Give it as owner/repo/path, not a URL — the host comes from the allow-list.';
	}
	const parts = said.split('/').filter(Boolean);
	if (parts.length < 2) {
		return `“${said}” is not owner/repo. A skill inside a repository is owner/repo/path/to/the/skill.`;
	}
	// The branch is a path segment too — it lands in a URL and in a tarball
	// prefix — so it is checked with the rest rather than trusted for being
	// after the @.
	for (const segment of branch ? [...parts, branch] : parts) {
		if (!SEGMENT.test(segment)) return `“${segment}” is not a usable path segment.`;
	}
	const project = `${parts[0]}/${parts[1]}`.toLowerCase();
	if (!sources.some((s) => s.toLowerCase() === project)) {
		return `${project} is not on the allow-list. Permitted: ${sources.join(', ') || 'nothing yet'}.`;
	}
	return '';
}

/** Shipped first, then yours, then installed — the order you would look in. */
export function inReadingOrder(rows: readonly SkillRow[]): SkillRow[] {
	const rank: Record<string, number> = { broken: 0, installed: 1, authored: 2, builtin: 3 };
	return [...rows].sort(
		(a, b) => (rank[a.source] ?? 9) - (rank[b.source] ?? 9) || a.name.localeCompare(b.name)
	);
}
