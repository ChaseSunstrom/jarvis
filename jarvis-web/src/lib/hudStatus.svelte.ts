// What the voice screen has to say about itself, for the bar above it.
//
// The top bar is drawn by the root layout on every route, and on `/` its
// readout is the pipeline's state — CONNECTING, LISTENING, PROCESSING — which
// lives in the page, not the layout. This is the one shared cell the page
// writes and the layout reads, so the bar can carry the voice screen's status
// without the page drawing a bar of its own.

export type HudTone = 'live' | 'warn' | 'off' | 'neutral';

export interface HudReadout {
	/** The word: STANDBY, LISTENING, PROCESSING, RESPONDING, OFFLINE… */
	label: string;
	tone: HudTone;
	/** Booting or connecting — nothing has answered yet. */
	busy: boolean;
	/** The pipeline state, for the tests and the dot's cadence. */
	state: string;
}

export const hudStatus = $state<HudReadout>({
	label: 'CONNECTING',
	tone: 'off',
	busy: true,
	state: 'idle'
});

/** Replace the readout wholesale. Fields, not the object, so subscribers see it. */
export function setHudStatus(next: HudReadout): void {
	hudStatus.label = next.label;
	hudStatus.tone = next.tone;
	hudStatus.busy = next.busy;
	hudStatus.state = next.state;
}
