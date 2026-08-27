// The instrument's geometry is a contract, and this is the web's half of it.
//
// `tests/contracts/reactor_geometry.json` says how many ticks, how many blades,
// which radius each ring sits at. `Reactor.svelte` types the same numbers as
// constants (it ships no JSON), `ReactorOrb.kt` types them again on the phone,
// and `android-app/tools/reactor_orb_test.py` holds the Kotlin to the file.
// This holds the Svelte to it — by rendering, so what is measured is what is
// drawn, and by reading the source, so a constant nobody draws yet cannot
// drift either.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import Reactor from './Reactor.svelte';

const CONTRACT = new URL('../../../../tests/contracts/reactor_geometry.json', import.meta.url);
const geo = JSON.parse(readFileSync(CONTRACT, 'utf8'));
const SOURCE = readFileSync(new URL('./Reactor.svelte', import.meta.url), 'utf8');

/** `const NAME = value;` out of the component. */
function constant(name: string): number {
	const m = SOURCE.match(new RegExp(`const ${name} = ([0-9.]+);`));
	if (!m) throw new Error(`Reactor.svelte declares no ${name}`);
	return Number(m[1]);
}

const SIZE = 320;
const R = SIZE / 2 - 4;

function drawn(props: Record<string, unknown> = {}): string {
	return render(Reactor as never, { props: { size: SIZE, ...props } as never }).body;
}

describe('the reactor and its contract', () => {
	it('types every geometric constant the contract names, with the same value', () => {
		const pairs: [string, string][] = [
			['TICKS', 'ticks'],
			['LONG_TICK_EVERY', 'long_tick_every'],
			['LONG_TICK_LEN', 'long_tick_len'],
			['SHORT_TICK_LEN', 'short_tick_len'],
			['BLADES', 'blades'],
			['BLADE_GAP_DEG', 'blade_gap_deg'],
			['R_BLADE', 'r_blade'],
			['BLADE_WIDTH_RATIO', 'blade_width_ratio'],
			['BLADE_WIDTH_MIN', 'blade_width_min'],
			['R_COIL', 'r_coil'],
			['R_LEVEL', 'r_level'],
			['LEVEL_WIDTH', 'level_width'],
			['R_CORE', 'r_core'],
			['IRIS_A_R', 'iris_a_r'],
			['IRIS_A_SWEEP', 'iris_a_sweep'],
			['IRIS_B_R', 'iris_b_r'],
			['IRIS_B_SWEEP', 'iris_b_sweep'],
			['R_THINK', 'r_think'],
			['DOT_RATIO', 'dot_ratio'],
			['DOT_MIN', 'dot_min'],
			['DOT_GLOW_RATIO', 'dot_glow_ratio'],
			['DOT_GLOW_MIN', 'dot_glow_min'],
			['IDLE_BREATH_LEVEL', 'idle_breath_level']
		];
		for (const [name, key] of pairs) {
			expect(constant(name), `${name} vs contract ${key}`).toBe(geo[key]);
		}
	});

	it('draws the bezel the contract describes: the ticks, and the long ones', () => {
		const body = drawn();
		const ticks = body.match(/<line /g) ?? [];
		expect(ticks.length).toBe(geo.ticks);
		const long = body.match(/class="[^"]*\blong\b[^"]*"/g) ?? [];
		expect(long.length).toBe(geo.ticks / geo.long_tick_every);
	});

	it('draws the blades at the blade radius, with the gap', () => {
		const body = drawn();
		const blades = body.match(/class="blade (idle|soft|done|running|pending)\b/g) ?? [];
		expect(blades.length).toBe(geo.blades);
		const arc = body.match(/A([0-9.]+) [0-9.]+ 0 0 1/);
		expect(Number(arc![1])).toBeCloseTo(R * geo.r_blade, 5);
	});

	it('places the coil, the level and the lens where the contract says', () => {
		const body = drawn();
		const radius = (cls: string) => Number(body.match(new RegExp(`class="${cls}[^"]*" cx="[0-9.]+" cy="[0-9.]+" r="([0-9.]+)"`))![1]);
		expect(radius('coil')).toBeCloseTo(R * geo.r_coil, 5);
		expect(radius('level-track')).toBeCloseTo(R * geo.r_level, 5);
		expect(radius('rim')).toBeCloseTo(R * geo.r_core, 5);
		expect(radius('think')).toBeCloseTo(R * geo.r_think, 5);
	});

	it('reports the level it was given, clamped, on the element', () => {
		expect(drawn({ level: 0.38 })).toContain('data-level="0.38"');
		expect(drawn({ level: 4 })).toContain('data-level="1.00"');
		expect(drawn({ level: -1 })).toContain('data-level="0.00"');
		expect(drawn({ level: Number.NaN })).toContain('data-level="0.00"');
	});

	it('wears each of the five states, and each is a different palette', () => {
		for (const state of geo.states) {
			expect(drawn({ state })).toContain(`data-state="${state}"`);
		}
		// The states are distinct in the stylesheet, not only in an attribute.
		for (const state of ['listening', 'thinking', 'speaking', 'error']) {
			expect(SOURCE).toContain(`[data-state='${state}']`);
			expect(SOURCE).toContain(`--jv-orb-${state}-blob-0`);
		}
	});

	it('groups the blades into plan steps when asked', () => {
		const body = drawn({ segments: { done: 2, running: 1, total: 5 } });
		expect(body).toContain('data-segments="2/5"');
		const done = body.match(/class="blade done\b/g) ?? [];
		const running = body.match(/class="blade running\b/g) ?? [];
		const pending = body.match(/class="blade pending\b/g) ?? [];
		expect(done.length + running.length + pending.length).toBe(geo.blades);
		expect(done.length).toBeGreaterThan(0);
		expect(running.length).toBeGreaterThan(0);
	});

	it('assembles layer by layer for the boot sequence', () => {
		const body = drawn({ reveal: { bezel: 1, blades: 0.5, coil: 0, level: 0, core: 0 } });
		expect(body).toContain('opacity: 0.5');
		expect(body).toContain('opacity: 0;');
	});

	it('names every period the contract names as a token', () => {
		for (const [, token] of Object.entries(geo.periods) as [string, string][]) {
			const cssName = '--jv-rx-' + token.split('.').pop();
			expect(SOURCE, `${cssName} is not used`).toContain(cssName);
		}
	});
});
