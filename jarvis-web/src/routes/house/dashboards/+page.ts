import { redirect } from '@sveltejs/kit';

/**
 * Moved again: a section of HOUSE from M48, a destination of its own since
 * M62. See `docs/UI_MIGRATION.md`.
 *
 * A redirect rather than a 404, for the same reason `/dashboards` redirected
 * here for thirteen milestones: a bookmark, a link in a note and a phone that
 * has not updated all point at this path. 308, so it is remembered.
 */
export function load() {
	redirect(308, '/dashboards');
}
