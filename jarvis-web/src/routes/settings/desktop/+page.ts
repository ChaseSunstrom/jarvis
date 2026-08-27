import { redirect } from '@sveltejs/kit';

/**
 * Moved into SETTINGS › Console (M54). See `docs/UI_MIGRATION.md` §3.
 *
 * The desktop page was two panels — this window, paired computers — about
 * the machines that show the console, and "the console" is the section for
 * that. A redirect rather than a 404, for the same reason `/desktop` itself
 * redirects: a bookmark and the `g e` chord both still land somewhere.
 */
export function load() {
	redirect(308, '/settings/console');
}
