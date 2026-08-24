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
}

export const SCREENS: Screen[] = [
	{
		path: '/',
		name: 'Voice',
		purpose: 'Talk to Jarvis, and watch the turn it is taking.',
		nav: false,
		probe: 'status'
	},
	{
		path: '/devices',
		name: 'Devices',
		purpose: 'Every entity, grouped by area, live — and the controls for them.',
		nav: true,
		probe: 'devices-lede'
	},
	{
		path: '/areas',
		name: 'Areas',
		purpose: 'The rooms voice commands resolve against, and what is in them.',
		nav: true,
		probe: 'areas-screen'
	},
	{
		path: '/automations',
		name: 'Automations',
		purpose: 'What runs by itself, when it last ran, and whether it is on.',
		nav: true,
		probe: 'automations-screen'
	},
	{
		path: '/tools',
		name: 'Tools',
		purpose: 'What Jarvis can call, what it is allowed to call, and MCP servers.',
		nav: true,
		probe: 'tools-screen'
	},
	{
		path: '/tasks',
		name: 'Tasks',
		purpose: 'Everything slow enough to ask about: running, scheduled, finished.',
		nav: true,
		probe: 'tasks-lede'
	},
	{
		path: '/dashboards',
		name: 'Dashboards',
		purpose: 'Graphs you arranged: Jarvis’s own numbers, this host, and anything else configured.',
		nav: true,
		probe: 'dashboards-screen'
	},
	{
		path: '/code',
		name: 'Code',
		purpose: 'Repositories Jarvis may work in, and the jobs it has run in them.',
		nav: true,
		probe: 'code-screen'
	},
	{
		path: '/settings',
		name: 'Settings',
		purpose: 'The backend’s own settings, pairing, voice identity and this console.',
		nav: true,
		probe: 'settings-screen'
	}
];

/** The screens the console's tab strip offers, in order. */
export const NAV_SCREENS = SCREENS.filter((screen) => screen.nav);

export function screenFor(path: string): Screen | undefined {
	return SCREENS.find((screen) => screen.path === path);
}
