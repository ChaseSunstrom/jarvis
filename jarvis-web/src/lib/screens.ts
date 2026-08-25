// The console's screens, as data.
//
// One entry per routed page. Three things read it: the nav builds itself from
// it, `e2e/states.spec.ts` drives every screen through all four states without
// anybody remembering to add the new one, and
// `scripts/verify/web_states_check.py` fails if a page exists that is not
// declared here — or a screen is declared that no page serves.
//
// `probe` is the element that proves the screen actually rendered: not a
// heading (every page has one, even a broken one), but something only the
// live screen draws.

export interface Screen {
	/** The route. */
	path: string;
	/** What it is called in the nav and in a test's failure message. */
	name: string;
	/** One line: what somebody comes to this screen to do. */
	purpose: string;
	/** In the console's tab strip? The HUD and the style guide are not. */
	nav: boolean;
	/** A `data-testid` the ready screen renders. */
	probe: string;
	/**
	 * The `g _` keyboard chord, for the screens that are in the tab strip.
	 *
	 * Here rather than in the layout, because the layout used to carry its own
	 * copy of the whole nav — eleven entries beside this file's nine, with
	 * `/notes` and `/desktop` in one and not the other. Two lists of the same
	 * thing is how a route ends up reachable and undeclared, which is what
	 * `web_states_check.py` found.
	 */
	chord?: string;
}

export const SCREENS: Screen[] = [
	{
		path: '/',
		name: 'Voice',
		purpose: 'Talk to Jarvis, and watch the turn it is taking.',
		nav: false,
		probe: 'status',
		chord: 'g h'
	},
	{
		path: '/devices',
		name: 'Devices',
		purpose: 'Every entity, grouped by area, live — and the controls for them.',
		nav: true,
		probe: 'devices-lede',
		chord: 'g d'
	},
	{
		path: '/areas',
		name: 'Areas',
		purpose: 'The rooms voice commands resolve against, and what is in them.',
		nav: true,
		probe: 'areas-screen',
		chord: 'g r'
	},
	{
		path: '/automations',
		name: 'Automations',
		purpose: 'What runs by itself, when it last ran, and whether it is on.',
		nav: true,
		probe: 'automations-screen',
		chord: 'g a'
	},
	{
		path: '/tools',
		name: 'Tools',
		purpose: 'What Jarvis can call, what it is allowed to call, and MCP servers.',
		nav: true,
		probe: 'tools-screen',
		chord: 'g t'
	},
	{
		path: '/tasks',
		name: 'Tasks',
		purpose: 'Everything slow enough to ask about: running, scheduled, finished.',
		nav: true,
		probe: 'tasks-lede',
		chord: 'g k'
	},
	{
		path: '/dashboards',
		name: 'Dashboards',
		purpose: 'Graphs you arranged: Jarvis’s own numbers, this host, and anything else configured.',
		nav: true,
		probe: 'dashboards-screen',
		chord: 'g b'
	},
	{
		path: '/code',
		name: 'Code',
		purpose: 'Repositories Jarvis may work in, and the jobs it has run in them.',
		nav: true,
		probe: 'code-screen',
		chord: 'g c'
	},
	{
		path: '/notes',
		name: 'Notes',
		purpose: 'What Jarvis has written down, and what it wrote it from.',
		nav: true,
		probe: 'notes-screen',
		chord: 'g n'
	},
	{
		path: '/memory',
		name: 'Memory',
		purpose: 'What Jarvis remembers about you — and the buttons to take it back.',
		nav: true,
		probe: 'memory-screen',
		chord: 'g m'
	},
	{
		path: '/desktop',
		name: 'Desktop',
		purpose: 'The machines running the desktop agent, and what they are doing.',
		nav: true,
		probe: 'desktop-lede',
		chord: 'g e'
	},
	{
		path: '/tasks/[id]',
		name: 'Task',
		purpose: 'One task: its steps, its tool calls and how it ended.',
		nav: false,
		probe: 'task-lede'
	},
	{
		path: '/settings',
		name: 'Settings',
		purpose: 'The backend’s own settings, pairing, voice identity and this console.',
		nav: true,
		probe: 'settings-screen',
		chord: 'g s'
	}
];

/** The screens the console's tab strip offers, in order. */
export const NAV_SCREENS = SCREENS.filter((screen) => screen.nav);

/**
 * `g _` chord → route, built from the list above.
 *
 * There used to be three of these: this file's `nav` flags, the layout's own
 * `NAV` array, and a hand-written table in `shortcuts.ts`. They disagreed —
 * `/notes` and `/desktop` were reachable and undeclared here, and the nav's
 * tooltip advertised `g b` for dashboards while the chord table had no such
 * entry, so the tooltip was promising a key that did nothing.
 */
export const CHORD_ROUTES: Readonly<Record<string, string>> = Object.fromEntries(
	SCREENS.filter((screen) => screen.chord).map((screen) => [screen.chord as string, screen.path])
);

export function screenFor(path: string): Screen | undefined {
	return SCREENS.find((screen) => screen.path === path);
}
