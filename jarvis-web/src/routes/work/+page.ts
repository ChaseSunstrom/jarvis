import { redirect } from '@sveltejs/kit';

/** `/work` is its first section. A redirect, never a second copy of it. */
export function load() {
	redirect(307, '/work/tasks');
}
