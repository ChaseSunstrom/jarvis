// SETTINGS, cut to what a person changes (M54).
//
// jarvis-core exposes its editable settings as rows in groups — Assistant,
// House, Voice — with a key, a type and a note written for an operator. The
// console used to render all of them, in those groups, key and all. That is a
// complete page and not a usable one: the person who opens SETTINGS wants to
// change the wake word, or the time zone, or which model answers, and had to
// find `voice.wake_word` among `jarvis.log_level` and `llm.options.num_ctx`.
//
// This is the cut. Five sections a person can name — Assistant · Voice · House
// · Console · Tools — each featuring the few rows somebody actually comes for,
// in plain words with one line saying why, and the rest of that section's
// rows behind an EVERYTHING fold exactly as the server describes them. Nothing
// is dropped: `settings.spec.ts` walks every key the server sends and finds
// it on one of the five.
//
// Pure data, so the plan is testable without a browser and the verify script
// can read it.

/** The five sections. `tools` has no server settings rows; it is here so the map is total. */
export type SettingsSectionId = 'assistant' | 'voice' | 'house' | 'console' | 'tools';

/** One featured row: which setting, what to call it, and why anybody would change it. */
export interface FeaturedSetting {
	key: string;
	/** Plain words. Never the key. */
	label: string;
	/** One line: what changing it does, in the user's terms. */
	why: string;
}

/**
 * Which server group lands on which section.
 *
 * The server's groups are its own — `jarvis.name` is in House because that is
 * the config block it lives in — and the plain rows below cut across them.
 * The EVERYTHING fold does not: it shows a group whole, where the server put
 * it, because a fold called everything that quietly omits a row is the kind
 * of small lie that teaches people to distrust the page.
 */
export const GROUP_SECTIONS: Readonly<Record<string, SettingsSectionId>> = {
	Assistant: 'assistant',
	Voice: 'voice',
	House: 'house'
};

/** A group the console has never heard of lands on Assistant, so it is still reachable. */
export const DEFAULT_SECTION: SettingsSectionId = 'assistant';

export function sectionOfGroup(group: string): SettingsSectionId {
	return GROUP_SECTIONS[group] ?? DEFAULT_SECTION;
}

/**
 * The featured rows, per section, in the order they are drawn.
 *
 * The models are not here: they are the MODELS panel above these rows, which
 * writes the same `llm.model` / `llm.fast_model` / `vision.model` settings
 * through the same API, from a list of what is actually served rather than
 * from a dropdown of aliases.
 */
export const FEATURED: Readonly<Record<SettingsSectionId, readonly FeaturedSetting[]>> = {
	assistant: [
		{
			key: 'llm.options.temperature',
			label: 'Temperature',
			why: 'How inventive the answers are. 0.7 is the usual place to start; lower is steadier.'
		},
		{
			key: 'jarvis.name',
			label: 'Name',
			why: 'What it calls itself when it speaks, and what the console calls it.'
		},
		{
			key: 'jarvis.language',
			label: 'Language',
			why: 'The language it answers in.'
		}
	],
	voice: [
		{
			key: 'voice.wake_word',
			label: 'Wake word',
			why: 'What you say to get its attention. Only the words the wake-word service has models for.'
		},
		{
			key: 'voice.tts_voice',
			label: 'Voice',
			why: 'What it sounds like. Only the voices the speech service is serving right now.'
		},
		{
			key: 'voice.speaker.mode',
			label: 'Who may speak',
			why: 'off: anyone. observe: Jarvis says who spoke, refuses nobody. enforce: a voice not enrolled is refused. Live once a voice is enrolled.'
		},
		{
			key: 'voice.tts.length_scale',
			label: 'Pace',
			why: 'How fast it speaks: Piper\u2019s length scale, 1.0 its own pace, 0.9 a tenth quicker. Set PIPER_LENGTH_SCALE in .env and restart Piper.'
		},
		{
			key: 'voice.language',
			label: 'Speech language',
			why: 'The language it listens for and speaks. Usually the same as the assistant’s.'
		}
	],
	house: [
		{
			key: 'demo.enabled',
			label: 'Demo mode',
			why: 'The fixture house — fake lights, a lock, a garage door, sensors — for trying Jarvis with no hardware. Off removes them at once; a real house wants it off.'
		},
		{
			key: 'jarvis.time_zone',
			label: 'Time zone',
			why: 'When “at seven” is. Every timed automation and every clock reads this.'
		},
		{
			key: 'jarvis.unit_system',
			label: 'Units',
			why: 'Metric or imperial, for every temperature, distance and speed it reports.'
		}
	],
	console: [],
	tools: []
};

/** Every featured key, for the test that asks whether a key is a plain row somewhere. */
export const FEATURED_KEYS: ReadonlySet<string> = new Set(
	Object.values(FEATURED).flatMap((rows) => rows.map((row) => row.key))
);

/** The featured rows of one section, in order. */
export function featuredOf(section: SettingsSectionId): readonly FeaturedSetting[] {
	return FEATURED[section];
}
