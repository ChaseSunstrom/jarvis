import { redirect } from '@sveltejs/kit';

/**
 * `/house` is its first section.
 *
 * A redirect rather than a copy of the section: two pages that render the same
 * thing are two pages that drift, and the one nobody opens drifts first.
 */
export function load() {
	redirect(307, '/house/devices');
}
