import { redirect } from '@sveltejs/kit';

/**
 * One task, moved under WORK by the consolidation (M48).
 *
 * The id is carried through: a link to a task is the link people actually
 * share, and dropping them at the list with "it moved" is not a redirect.
 */
export function load({ params }: { params: { id: string } }) {
	redirect(308, `/work/tasks/${params.id}`);
}
