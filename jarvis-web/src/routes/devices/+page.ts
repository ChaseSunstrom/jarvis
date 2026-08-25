import { redirect } from '@sveltejs/kit';

/**
 * Moved by the consolidation (M48). See `docs/UI_MIGRATION.md`.
 *
 * A redirect rather than a 404: a bookmark, a link somebody put in a note, and
 * the phone's own tab strip all point here, and "it used to work" is the worst
 * thing a console can say. 308, so it is remembered.
 */
export function load() {
	redirect(308, '/house/devices');
}
