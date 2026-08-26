// What the KNOWLEDGE destination's parts tell each other.
//
// The layout draws the graph from its own reading of the notes and the memory
// lists; the sections under it edit those lists over their own connections.
// Without a word between them, a note saved in the editor would sit in the
// graph as it was until the next reload. This is the one shared cell: a
// section bumps `version` after it changes anything, the layout re-reads.
// Nothing else crosses — the selection is the URL, so a link to a note is a
// link to a node.

export const knowledge = $state({ version: 0 });

/** Something in the notes or the memory changed; whoever draws them should re-read. */
export function touchKnowledge(): void {
	knowledge.version += 1;
}
