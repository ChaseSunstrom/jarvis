import { redirect } from '@sveltejs/kit';

/** `/knowledge` is its first section. A redirect, never a second copy of it. */
export function load() {
	redirect(307, '/knowledge/notes');
}
