import { redirect } from '@sveltejs/kit';

/**
 * Moved by the consolidation (M48), and again into SETTINGS › Console (M54).
 * See `docs/UI_MIGRATION.md`.
 *
 * A redirect rather than a 404: a bookmark, a link somebody put in a note, and
 * the phone's own tab strip all point here, and "it used to work" is the worst
 * thing a console can say. 308, so it is remembered — and straight to where
 * the panels are now, not through the address they had in between.
 */
export function load() {
	redirect(308, '/settings/console');
}
