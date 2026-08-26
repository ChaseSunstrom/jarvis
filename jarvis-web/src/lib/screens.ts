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
	/** What it is called in the nav and in a test's failure message. Unique. */
	name: string;
	/**
	 * The word on the section strip, when `name` is longer than it.
	 *
	 * `name` has to be unique — every per-screen spec titles its tests with
	 * it, and Playwright refuses two tests of one title — so the settings
	 * section about the house is named `House settings` and labelled HOUSE.
	 */
	label?: string;
	/** One line: what somebody comes to this screen to do. */
	purpose: string;
	/** In the TOP-LEVEL tab strip? A section is not; its destination is. */
	nav: boolean;
	/**
	 * The voice screen.
	 *
	 * In the bar — it is the first tab, as Reactor II draws it — but not a
	 * console front door the phone mirrors: on the phone the voice screen is
	 * native, so `console_parity_test.py` binds the phone's strip to the
	 * `nav && !hud` five.
	 */
	hud?: boolean;
	/**
	 * The destination this is a section of, e.g. `/house`.
	 *
	 * Empty for a destination itself and for anything outside the four. The
	 * console has four primary destinations rather than eleven (M48): a
	 * section lives inside one, switched by a strip that persists while its
	 * content changes, and has a real URL so a link to it still works.
	 */
	within?: string;
	/** A `data-testid` the ready screen renders. */
	probe: string;
	/**
	 * The `g _` keyboard chord.
	 *
	 * On SECTIONS rather than destinations, so every chord anybody learnt still
	 * lands where its page now lives: `g d` was devices and reaches devices,
	 * inside HOUSE. Consolidating the nav was allowed to cost a click; it was
	 * not allowed to cost a keystroke somebody already knows.
	 *
	 * Here rather than in the layout or in `shortcuts.ts`, both of which used
	 * to carry their own copy — nine entries, eleven and ten, disagreeing, with
	 * the nav's tooltip advertising a `g b` for dashboards that no table had.
	 * It works now.
	 */
	chord?: string;
}

export const SCREENS: Screen[] = [
	{
		path: '/',
		name: 'Voice',
		purpose: 'Talk to Jarvis, and watch the turn it is taking.',
		nav: true,
		hud: true,
		probe: 'status',
		chord: 'g h'
	},

	// --- the four console destinations -----------------------------------
	// A destination since M62, and the first console tab: the dashboard is
	// what a person opens the console to look at, and as a section of HOUSE it
	// sat two taps deep behind the device list. Its own path is the page —
	// there are no sections — so it is the one destination that does not
	// redirect. (Comments stay above the brace: m48's gate counts blocks that
	// open on `path:`.)
	{
		path: '/dashboards',
		name: 'Dashboards',
		purpose: 'The house at a glance: the graphs and readings somebody arranged.',
		nav: true,
		probe: 'dashboards-screen',
		chord: 'g b'
	},
	{
		path: '/house',
		name: 'House',
		purpose: 'What is on, where it is, what it has been doing, and the rules that run themselves.',
		nav: true,
		probe: 'house-screen'
	},
	{
		path: '/work',
		name: 'Work',
		purpose: 'What Jarvis is doing or has done for you: tasks, research, coding jobs.',
		nav: true,
		probe: 'work-screen'
	},
	{
		path: '/knowledge',
		name: 'Knowledge',
		purpose: 'What Jarvis has written down, and what it remembers about you.',
		nav: true,
		probe: 'knowledge-screen'
	},
	{
		path: '/settings',
		name: 'Settings',
		purpose: 'Configuration and capability: settings, tools, what is installed, the machines it runs on.',
		nav: true,
		probe: 'settings-screen'
	},

	// --- their sections --------------------------------------------------
	{
		path: '/house/devices',
		name: 'Devices',
		purpose: 'Every entity, grouped by area, live — and the controls for them.',
		nav: false,
		within: '/house',
		probe: 'devices-lede',
		chord: 'g d'
	},
	{
		path: '/house/areas',
		name: 'Areas',
		purpose: 'The rooms voice commands resolve against, and what is in them.',
		nav: false,
		within: '/house',
		probe: 'areas-screen',
		chord: 'g r'
	},
	{
		path: '/house/automations',
		name: 'Automations',
		purpose: 'The rules that run themselves, their traces, and the editor.',
		nav: false,
		within: '/house',
		probe: 'automations-screen',
		chord: 'g a'
	},
	{
		path: '/work/tasks',
		name: 'Tasks',
		purpose: 'Everything running or finished, with its steps and its tool calls.',
		nav: false,
		within: '/work',
		probe: 'tasks-lede',
		chord: 'g k'
	},
	{
		path: '/work/tasks/[id]',
		name: 'Task',
		purpose: 'One task: its steps, its tool calls and how it ended.',
		nav: false,
		probe: 'task-lede'
	},
	{
		path: '/work/code',
		name: 'Code',
		purpose: 'Coding jobs, the repositories they run in, and what they changed.',
		nav: false,
		within: '/work',
		probe: 'code-lede',
		chord: 'g c'
	},
	{
		path: '/knowledge/notes',
		name: 'Notes',
		purpose: 'What Jarvis has written down, and what it wrote it from.',
		nav: false,
		within: '/knowledge',
		probe: 'notes-lede',
		chord: 'g n'
	},
	{
		path: '/knowledge/memory',
		name: 'Memory',
		purpose: 'What Jarvis remembers about you, and where each fact came from.',
		nav: false,
		within: '/knowledge',
		probe: 'memory-lede',
		chord: 'g m'
	},
	// SETTINGS, cut to what a person changes (M54): five sections a person can
	// name. The plan of which setting is featured where is `sections/settingsPlan.ts`.
	{
		path: '/settings/assistant',
		name: 'Assistant',
		purpose: 'The models it runs on, and how it answers: temperature, its name, its language.',
		nav: false,
		within: '/settings',
		probe: 'assistant-screen',
		chord: 'g s'
	},
	{
		path: '/settings/voice',
		name: 'Voice settings',
		label: 'Voice',
		purpose: 'The wake word, the voice it speaks with, and whose voice it answers.',
		nav: false,
		within: '/settings',
		probe: 'settings-voice-lede',
		chord: 'g v'
	},
	{
		path: '/settings/house',
		name: 'House settings',
		label: 'House',
		purpose: 'Where the house is: the time zone, the units, and the rooms.',
		nav: false,
		within: '/settings',
		probe: 'settings-house-lede'
	},
	{
		path: '/settings/console',
		name: 'Console',
		purpose: 'This screen: text size, how it reaches Jarvis, pairing a phone, the desktop shell.',
		nav: false,
		within: '/settings',
		probe: 'settings-console-lede',
		chord: 'g e'
	},
	{
		path: '/settings/tools',
		name: 'Tools',
		purpose: 'What Jarvis can call, what it is allowed to call, and what is installed.',
		nav: false,
		within: '/settings',
		probe: 'tools-screen',
		chord: 'g t'
	}
];

/** The top-level tabs, in order: the voice screen and the five console destinations. */
export const NAV_SCREENS = SCREENS.filter((screen) => screen.nav);

/** The console's front doors — what the phone's native strip mirrors. */
export const CONSOLE_SCREENS = NAV_SCREENS.filter((screen) => !screen.hud);

/** The sections of one destination, in declaration order. */
export function sectionsOf(destination: string): Screen[] {
	return SCREENS.filter((screen) => screen.within === destination);
}

/**
 * Where a path from before the consolidation lives now.
 *
 * Every old top-level route redirects rather than 404s: a bookmark, a link in
 * a note and the phone's own tab strip all point at these, and "it used to
 * work" is the worst thing a console can say.
 */
export const MOVED: Readonly<Record<string, string>> = {
	'/devices': '/house/devices',
	'/areas': '/house/areas',
	// A section of HOUSE from M48 to M61, a destination since M62.
	'/house/dashboards': '/dashboards',
	'/automations': '/house/automations',
	'/tasks': '/work/tasks',
	'/code': '/work/code',
	'/notes': '/knowledge/notes',
	'/memory': '/knowledge/memory',
	'/tools': '/settings/tools',
	// The desktop page became two panels on SETTINGS › Console (M54);
	// `/settings/desktop` itself redirects there too.
	'/desktop': '/settings/console'
};

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
