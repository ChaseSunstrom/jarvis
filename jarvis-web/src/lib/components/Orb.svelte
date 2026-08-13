<script lang="ts">
	import { onMount } from 'svelte';
	import { prefersReducedMotion, watchReducedMotion } from '$lib/motion';

	let {
		level = 0,
		orbState = 'idle'
	}: { level?: number; orbState?: 'idle' | 'listening' | 'thinking' | 'speaking' } = $props();

	const STATE_NUM: Record<string, number> = { idle: 0, listening: 1, thinking: 2, speaking: 3 };

	let canvas: HTMLCanvasElement | undefined = $state();
	let webglOk = $state(true);
	let smoothLevel = 0;
	let smoothState = 0;

	/**
	 * Draw one frame, on demand.
	 *
	 * Bound by `onMount` and used by the effect below: with the animation loop
	 * off, the orb still has to be repainted when the pipeline changes state,
	 * because its colour is information — which of five things Jarvis is doing —
	 * and not decoration. Null before mount and after destroy.
	 */
	let redraw: (() => void) | null = null;

	$effect(() => {
		// Tracked so a state change repaints a paused orb. `level` is deliberately
		// NOT tracked: it changes with every audio frame, and following it here
		// would be the animation loop again under another name.
		orbState;
		redraw?.();
	});

	const TAU = Math.PI * 2;

	/**
	 * Blob-drift rate per state, turns per second. The same table the phone's
	 * `SiriPalette.orbitHz` holds — 3.5s at rest down to 1s while thinking — so
	 * the browser and the phone show one object moving at one speed.
	 */
	const ORBIT_HZ = [1 / 3.5, 1 / 1.4, 1 / 1.0, 1 / 1.2];

	/** Breathing period per state, seconds. Also the phone's. */
	const BREATH_S = [3.5, 1.4, 1.0, 1.2];

	/**
	 * The three blobs orbit at 1 : 0.73 : 1.31 so the field never returns to the
	 * same arrangement. Each one integrates its OWN phase, wrapped at TAU on its
	 * own — multiplying one shared phase by 0.73 puts a discontinuity in the
	 * second and third blobs every time that shared phase wraps, which is a
	 * visible jump once per orbit and exactly the loop seam this is supposed to
	 * be free of.
	 */
	const BLOB_RATE = [1.0, 0.73, 1.31];

	/** Seconds for the key light to walk once around its small drift path. */
	const DRIFT_S = 17;

	/**
	 * Mirrors of the shader's own constants, needed here because the coil
	 * pattern's phase is integrated in PATTERN turns rather than in physical
	 * radians — see coilAt(). Wrapping a physical-radian spin at TAU moved the
	 * plates by SPOKE_SPIN_RATIO * SPOKE_COUNT = 3.5 segments per wrap, and the
	 * half segment was a visible eighteen-degree jump every time round.
	 */
	const SPOKE_SPIN_RATIO = 0.35;
	const SPOKE_COUNT = 10;

	const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);
	const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

	/** Sample a four-state table at a fractional state, the way the shader does. */
	const mix4 = (v: number[], s: number) =>
		lerp(lerp(lerp(v[0], v[1], clamp01(s)), v[2], clamp01(s - 1)), v[3], clamp01(s - 2));

	const VERT = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`;

	/*
	 * The arc reactor, as one lit object.
	 *
	 * This is the same reactor the Android app draws (`ReactorOrb.kt`), with the
	 * same proportions, the same palette and the same rates — the two are pinned
	 * against each other by `android-app/tools/reactor_orb_test.py`.
	 *
	 * The whole of "make it look 3D and not like a clock face" is that nothing
	 * here is shaded by RADIUS. A stack of concentric shapes whose brightness is
	 * a function of `r` is a disc with rings on it however many rings you add.
	 * Every shaded term below goes through the sphere normal instead:
	 *
	 *   * n.l off a key light fixed up and to the left, so the ball has a near
	 *     side and a far side and the terminator does not sit on a circle;
	 *   * a cool fill from the lower right, because a single light in a black
	 *     room gives you a crescent moon rather than a glass ball;
	 *   * two Blinn-Phong lobes — one tight and bright, one wide and dim — off a
	 *     glass cover, drifting slowly. The tight one is most of what sells the
	 *     object: an off-centre catchlight is the single strongest sphere cue
	 *     there is;
	 *   * fresnel at the limb, pow(1 - n.z, 5), so the EDGE comes up brighter
	 *     than the middle at grazing angles. That is what makes it read as a
	 *     surface curving away rather than as a radial gradient;
	 *   * occlusion that is actually computed: the coil assembly casts onto the
	 *     substrate along the light's own screen-space direction, the groove the
	 *     plates lie in is darkest hard against its walls, and each plate is lit
	 *     by where it sits on the sphere — the ones at the top catch the key,
	 *     the ones at the bottom sit in the assembly's own shade;
	 *   * a bloom pass that is wider and dimmer than the object and is NOT
	 *     masked by it, so light spills past the ball and over the chrome. Light
	 *     in the air reads as volume; a brighter core just reads as a brighter
	 *     core.
	 *
	 * Everything outside the ball — coils excepted — is instrument chrome, drawn
	 * flat on purpose. A gauge is a thing bolted to the front, not part of the
	 * sphere, and shading it would make the assembly read as one moulded lump.
	 *
	 * Phases arrive as uniforms rather than being derived from uTime: the drift
	 * and spin rates change with the state, and `phase = t * rate` jumps by
	 * `t * delta` the instant the rate moves. They are integrated per frame on
	 * the CPU instead, exactly as both Android views integrate theirs.
	 */
	const FRAG = `
// highp wherever the hardware has it, because several terms below multiply a
// WRAPPING phase by a large integer. The worst is the fine dash ring,
// sin(ang * 64.0 - spinRad * 92.0): spinRad is uSpin, a free-running phase
// wrapped at TAU, so that term alone runs to 578. mediump is only guaranteed
// ten bits of mantissa, which up there is an ulp of over half a radian — most
// of a dash — so the ring would strobe rather than turn on any GPU that takes
// the qualifier at its word. That is a lot of phones; desktop drivers give you
// highp whatever you ask for, so this is invisible in the browser it is
// written in. The 72-tick gauge has the same problem an order of magnitude
// smaller. reactor_orb_test.py does the arithmetic rather than trusting this
// comment, and also checks the #ifdef: highp is OPTIONAL in a WebGL 1 fragment
// shader, and asking for it unguarded fails to COMPILE where it is absent.
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes;
uniform float uTime;
uniform float uLevel;   // 0..1 audio energy, already gained
uniform float uState;   // 0 idle, 1 listen, 2 think, 3 speak (fractional while blending)
uniform vec3 uPhases;   // the three blob orbits, radians, each free-running
uniform float uSpin;    // chrome rotation, radians, free-running
uniform float uCoilSpin;// coil-pattern rotation, in PATTERN turns * TAU (below)
uniform float uBreath;  // breathing, radians, free-running
uniform float uDrift;   // key-light wander, radians, free-running

const float PI = 3.14159265;
const float TAU = 6.2831853;
const float INV2PI = 0.15915494;

// --- geometry, as multiples of the ball's radius. Mirrors ReactorOrb. -------
const float BALL          = 0.52;
const float BLOB_FRACTION = 0.80;
const float ORBIT_FRACTION= 0.30;
const float CORE_FRACTION = 0.30;
const float CORE_LEVEL_GAIN = 0.10;
const float SPOKE_INNER   = 0.42;
const float SPOKE_OUTER   = 0.92;
const float SPOKE_COUNT   = 10.0;
// The gap between two plates, in degrees, AT THE COIL ANNULUS' CENTRELINE. Its
// arc length is what is held fixed, not its angle — see the coil block below.
const float SPOKE_GAP_DEG = 9.0;
// The plates' lighting rule, and the same two numbers ReactorOrb states:
//
//     PLATE_LIGHT_BASE + PLATE_LIGHT_GAIN * max(0, cos(plate - light))
//
// They sum to 1, so the plate facing the light is exactly as bright as every
// plate was before any of them were lit — the ring gains its depth by the
// others giving some up, not by the assembly getting hotter. A Canvas has no
// per-pixel shader, so the phone samples that cosine once per plate; this
// samples the same cosine per pixel. Same rule, same numbers, and
// reactor_orb_test.py compares them rather than trusting that both files were
// retuned in step.
const float PLATE_LIGHT_BASE = 0.50;
const float PLATE_LIGHT_GAIN = 0.50;
// SPOKE_SPIN_RATIO is deliberately NOT here. The coil pattern's rotation rate
// is applied on the CPU now, where uCoilSpin is integrated (see the script
// block) — a copy of it down here would be dead, and editing the dead one would
// look like it should work and do nothing at all.
// The recess the plates lie in. Wider than the coil annulus at both ends:
// inside SPOKE_INNER it is the dark gap between the core and the assembly and
// the seat for the hub ring, outside SPOKE_OUTER it is the lip whose shadow
// falls back across the plates.
const float HOUSING_INNER = 0.34;
const float HOUSING_OUTER = 0.965;
const float HUB_FACTOR    = 0.385;
const float SEAT_SHADOW_SPAN = 0.16;
// How far the plates stand proud of the substrate, in ball radii. Only used to
// throw their shadow, so it is a shadow LENGTH more than a height.
const float COIL_LIFT     = 0.085;
const float INNER_RIM_FACTOR = 1.05;
const float TURBULENCE_FACTOR= 1.14;
const float MID_DASH_FACTOR  = 1.22;
const float FINE_DASH_FACTOR = 1.36;
const float GAUGE_FACTOR     = 1.50;
const float SWEEP_INNER_FACTOR = 1.10;
const float OUTER_FACTOR     = 1.70;
const float MINOR_TICK    = 0.08;
const float MAJOR_TICK    = 0.15;
const float HALO_FRACTION = 1.30;

const vec3 SUBSTRATE = vec3(0.024, 0.043, 0.086);   // #060B16
const vec3 HOUSING   = vec3(0.004, 0.012, 0.039);   // #01030A, darker than the
                                                    // ball: a hole cut in it
const vec3 HUB_METAL = vec3(0.659, 0.741, 0.824);   // #A8BDD2

/**
 * The palette, sampled at a fractional state so a transition is a colour
 * moving across the orb rather than a different orb. Same hexes as
 * SiriPalette.blobs / SiriPalette.core.
 */
void palette(float s, out vec3 b0, out vec3 b1, out vec3 b2, out vec3 core) {
	float w1 = clamp(s, 0.0, 1.0);
	float w2 = clamp(s - 1.0, 0.0, 1.0);
	float w3 = clamp(s - 2.0, 0.0, 1.0);

	// idle #2BB0D8 #3A6FE0 #29D8C0 / #DFF6FF
	vec3 i0 = vec3(0.169, 0.690, 0.847);
	vec3 i1 = vec3(0.227, 0.435, 0.878);
	vec3 i2 = vec3(0.161, 0.847, 0.753);
	vec3 ic = vec3(0.875, 0.965, 1.000);
	// listening #3FD8FF #5A8CFF #54FFE0 / #EBFDFF
	vec3 l0 = vec3(0.247, 0.847, 1.000);
	vec3 l1 = vec3(0.353, 0.549, 1.000);
	vec3 l2 = vec3(0.329, 1.000, 0.878);
	vec3 lc = vec3(0.922, 0.992, 1.000);
	// thinking #FF9E2C #FF5FA2 #C46BFF / #FFE9CC
	vec3 t0 = vec3(1.000, 0.620, 0.173);
	vec3 t1 = vec3(1.000, 0.373, 0.635);
	vec3 t2 = vec3(0.769, 0.420, 1.000);
	vec3 tc = vec3(1.000, 0.914, 0.800);
	// speaking #FFCF5C #FF9A3C #FF7BC0 / #FFF3D2
	vec3 s0 = vec3(1.000, 0.812, 0.361);
	vec3 s1 = vec3(1.000, 0.604, 0.235);
	vec3 s2 = vec3(1.000, 0.482, 0.753);
	vec3 sc = vec3(1.000, 0.953, 0.824);

	b0   = mix(mix(mix(i0, l0, w1), t0, w2), s0, w3);
	b1   = mix(mix(mix(i1, l1, w1), t1, w2), s1, w3);
	b2   = mix(mix(mix(i2, l2, w1), t2, w2), s2, w3);
	core = mix(mix(mix(ic, lc, w1), tc, w2), sc, w3);
}

float ring(float r, float rr, float th) { return smoothstep(th, 0.0, abs(r - rr)); }

float tickDist(float ang, float count) {
	float a = ang * INV2PI + 0.5;
	float f = fract(a * count);
	return min(f, 1.0 - f);
}

/** One blob's contribution: 1 at its centre, 0 at its own radius. */
float blob(vec2 p, vec2 centre, float radius) {
	return smoothstep(1.0, 0.0, length(p - centre) / max(radius, 1e-3));
}

/**
 * Coil coverage at an arbitrary point of ball-space, 0..1.
 *
 * Lifted out of main so the same plates can be sampled twice: once where they
 * are, and once offset along the light to find out what they are standing on
 * top of. An object that does not occlude anything is a sticker.
 */
// spin is uCoilSpin: the plate pattern's own rotation, counted in pattern
// turns rather than in physical radians (the CPU has already multiplied by
// SPOKE_SPIN_RATIO * SPOKE_COUNT). That is the whole trick to the wrap. The
// pattern repeats every TAU/SPOKE_COUNT of angle, so feeding it physical
// radians wrapped at TAU shifted the pattern by SPOKE_SPIN_RATIO * SPOKE_COUNT
// = 3.5 segments per wrap — and the half is a hard jump of eighteen degrees,
// once every wrap, forever. Counted in pattern turns, a TAU wrap moves it by
// exactly one whole segment, which is no movement at all.
float coilAt(vec2 pp, float spin) {
	float qq = length(pp);
	float band = smoothstep(SPOKE_INNER - 0.012, SPOKE_INNER + 0.008, qq)
	           * smoothstep(SPOKE_OUTER + 0.012, SPOKE_OUTER - 0.008, qq);
	// Also keeps atan away from the origin, where it is undefined.
	if (band <= 0.0) return 0.0;

	float aa = atan(pp.y, pp.x);
	// A constant-WIDTH gap rather than a constant-ANGLE one: SPOKE_GAP_DEG is
	// measured at the annulus' centreline and its arc LENGTH is held fixed, so
	// it opens out toward the middle and each plate comes out a keystone —
	// wider at the outer seat than at the inner one. A constant angle gives ten
	// identical sectors, and ten identical sectors is a pie chart.
	float segSpan = 360.0 / SPOKE_COUNT;
	float gapArc = SPOKE_GAP_DEG * (SPOKE_INNER + SPOKE_OUTER) * 0.5;
	float halfPlate = max((segSpan - gapArc / max(qq, SPOKE_INNER * 0.5)) * 0.5, 0.0);
	float segDeg = (fract(aa * INV2PI * SPOKE_COUNT - spin * INV2PI) - 0.5) * segSpan;
	return band * smoothstep(halfPlate, halfPlate - 1.4, abs(segDeg));
}

/** A gaussian splat, for the bloom pass. Falls to nothing by about 2 radii. */
float softGlow(vec2 pp, vec2 centre, float radius) {
	float d = length(pp - centre) / max(radius, 1e-3);
	return exp(-2.2 * d * d);
}

/**
 * Screen blend: what the phone's canvas does with PorterDuff.Mode.SCREEN.
 *
 * Not an optimisation and not a detail: the three blobs overlap almost all the
 * time, and plain addition takes their sum well past 1 in the middle, where it
 * clips to white and erases both the colour and the coils under it. Screen
 * brightens toward the sum and cannot exceed 1, which is exactly why the phone
 * uses it — and using anything else here is the two surfaces disagreeing about
 * the one thing the orb is for.
 */
vec3 screen(vec3 a, vec3 b) { return 1.0 - (1.0 - a) * (1.0 - b); }

void main() {
	vec2 uv = (gl_FragCoord.xy * 2.0 - uRes) / max(min(uRes.x, uRes.y), 1.0);
	float r = length(uv);
	// Nudged off the origin before atan, which is undefined at (0,0) and can
	// hand back a NaN that then poisons every term it is added to.
	vec2 angv = uv + vec2(step(r, 1e-4), 0.0);
	float ang = atan(angv.y, angv.x);
	vec2 radial = uv / max(r, 1e-3);
	float lvl = clamp(uLevel, 0.0, 1.0);
	float thinking = step(1.5, uState) * step(uState, 2.5);

	vec3 b0, b1, b2, coreCol;
	palette(uState, b0, b1, b2, coreCol);
	vec3 rimCol = b0;

	// The ball breathes and swells with the voice. Everything else is a
	// multiple of it, and OUTER_FACTOR * BALL stays inside the viewport.
	float breath = 1.0 + 0.04 * sin(uBreath);
	float R = max(BALL * breath * (1.0 + 0.06 * lvl), 1e-3);
	float q = r / R;                       // 0 at the centre, 1 at the ball's edge
	vec2 p = uv / R;                       // ball-space, for the blobs

	vec3 acc = vec3(0.0);
	float alpha = 0.0;

	// ---- the sphere, and the rig that lights it ---------------------------
	// One device pixel, in uv units and then in ball-space units, so the
	// silhouette gets exactly as much antialiasing as it needs at whatever size
	// the orb is being drawn and not a percentage of the radius.
	float px = 2.0 / max(min(uRes.x, uRes.y), 1.0);
	float aa = max(px / R, 0.0015);
	// A hard edge, softened by that one pixel. Fading the mask out over the
	// outer seven percent of the radius — which is what a fixed 0.93 does —
	// costs twice: the silhouette goes mushy, and it multiplies the fresnel to
	// nothing over exactly the band where fresnel is the entire point. A sphere
	// that trails off into its background is not a sphere.
	float ball = smoothstep(1.0, 1.0 - 1.6 * aa, q);
	// The surface normal. z is its component toward the viewer, and having it
	// is the difference between a ball and a circle: everything below is shaded
	// by n rather than by q.
	float qc = min(q, 1.0);
	float z = sqrt(max(1.0 - qc * qc, 0.0));
	vec3 n = normalize(vec3(p.x, p.y, z) + vec3(0.0, 0.0, 1e-3));
	vec3 V = vec3(0.0, 0.0, 1.0);

	// The key, up and to the left, wandering a couple of degrees. The wander is
	// built out of INTEGER harmonics of uDrift so it is smooth across the wrap
	// at TAU — a non-integer multiple would kink the light's path once a cycle,
	// and a kink in the highlight is the one thing the eye is guaranteed to
	// catch. Fixed here rather than inside the glass block because the
	// housing's machined parts are struck by it too: a hub ring lit from
	// somewhere else is a hub ring that reads as a decal.
	//
	// The amplitudes are small on purpose and the spec caps them. The phone
	// cannot swing its light: it fakes dot(n, L) with gradients struck about an
	// offset point, and ReactorOrb only breathes that offset's LENGTH by six
	// percent — its highlight never changes angle. These four numbers used to
	// come to a peak swing of ten degrees, so the browser's highlight walked a
	// fifth of the way round the ball while the phone's stayed nailed to one
	// bearing, which is the same "two orbs lit from two places" drift this
	// whole rig exists to prevent, arriving slowly instead of all at once.
	// At these amplitudes the swing is under three degrees: felt, not seen.
	float kx = 0.018 * sin(uDrift) + 0.008 * sin(uDrift * 3.0 + 1.7);
	float ky = 0.015 * cos(uDrift * 2.0) + 0.005 * sin(uDrift * 5.0 + 0.6);
	vec3 L = normalize(vec3(-0.46 + kx, 0.54 + ky, 0.70));
	// The key flattened onto the screen and renormalised — ReactorOrb's
	// LIGHT_DIR_X/LIGHT_DIR_Y, which is what every screen-space cosine on the
	// phone is taken against. L.xy on its own is only 0.71 long, so a cosine
	// against it can never reach 1 and anything scaled by it is quietly 29%
	// dark at the very point the light is pointing.
	vec2 Lxy = normalize(L.xy);
	// A dim cool fill from the opposite corner. One light in a black room gives
	// a crescent moon; the fill is what leaves the far side readable.
	vec3 Fl = normalize(vec3(0.60, -0.46, 0.64));
	vec3 H = normalize(L + V);
	vec3 Hf = normalize(Fl + V);

	float ndl = dot(n, L);
	float diff = clamp(ndl, 0.0, 1.0);
	// Wrapped diffuse. A self-luminous ball has no true terminator, so the
	// falloff is pushed round past 90 degrees rather than cut off at it — but
	// only just past. Wrap it much further than this and n.l stops varying
	// enough to be seen, which is the same as not having computed it.
	float wrap = clamp(ndl * 0.78 + 0.26, 0.0, 1.0);
	float fill = clamp(dot(n, Fl), 0.0, 1.0);
	float ndh = clamp(dot(n, H), 0.0, 1.0);
	// Fresnel, at two exponents. The pow-5 is the physical one and it lives in
	// the last two percent of the radius — on its own it is a hairline, too
	// thin at this size to say anything about shape. The broad one underneath
	// it is what actually bends the silhouette; the tight one on top is what
	// makes the very edge look wet.
	float grazing = clamp(1.0 - n.z, 0.0, 1.0);
	float fresBroad = pow(grazing, 2.5);
	float fresTight = pow(grazing, 5.0);

	// The dark ground the colours are lit against, itself shaded — a flat
	// substrate under a lit field is a hole in the illusion at every point the
	// field happens to be thin.
	acc += SUBSTRATE * ball * (0.55 + 0.75 * wrap);
	alpha += ball * 0.90;

	// Three drifting blobs, each on its own free-running phase so the field
	// never returns to an arrangement it has held before.
	float orbit = ORBIT_FRACTION * (0.75 + 0.25 * lvl);
	float br = BLOB_FRACTION * (1.0 + 0.10 * lvl);
	vec2 c0 = vec2(cos(uPhases.x), sin(uPhases.x) * 0.72) * orbit;
	vec2 c1 = vec2(cos(uPhases.y + 2.0943951), sin(uPhases.y + 2.0943951) * 0.72) * orbit;
	vec2 c2 = vec2(cos(uPhases.z + 4.1887902), sin(uPhases.z + 4.1887902) * 0.72) * orbit;
	vec3 field = screen(
		screen(b0 * blob(p, c0, br), b1 * blob(p, c1, br)),
		b2 * blob(p, c2, br)
	);
	// Screen is the right blend and it has one cost: three overlapping blobs
	// climb toward white in the middle, and white is the one colour the state
	// palette cannot say anything with. Pushing saturation back out from the
	// luminance restores the hue the overlap ate, so idle stays unmistakably
	// cyan and thinking unmistakably hot where the three cross.
	float lum = dot(field, vec3(0.299, 0.587, 0.114));
	field = max(mix(vec3(lum), field, 1.30), vec3(0.0));
	// Lit as a surface, in two independent parts, because they answer different
	// questions and multiplying them is what a sphere does:
	//
	//   shade  — WHICH WAY this bit of surface points relative to the lights.
	//            An ambient floor, n.l off the key, and the cool fill.
	//   facing — HOW FAR it has turned away from the eye. Nothing to do with
	//            the lights; it is the reason the colour crowds up at the
	//            silhouette instead of running flat to the edge.
	//
	// Left as one flat multiplier — or as a function of q — the field fills the
	// disc corner to corner at one brightness, and no amount of ring detail on
	// top of that will stop it reading as a painted circle.
	float shade = 0.14 + 0.72 * wrap + 0.16 * fill;
	float facing = 0.24 + 0.76 * z;
	acc += field * ball * shade * facing;
	alpha += ball * 0.10;

	// ---- the coil assembly, outward ---------------------------------------
	// Ten plates recessed in a housing between two seat rings. The single
	// element that most says "arc reactor" rather than "orb", and the blob
	// field lights them rather than being covered by them.

	float coil = coilAt(p, uCoilSpin);

	// Their shadow, before the plates themselves, because it falls on what is
	// already down. Sampling the plates at p + L.xy * lift asks "is there a
	// plate between this point and the light" — so the shade lands down and to
	// the right of each plate, away from a key that is up and to the left.
	//
	// Masked by (1 - coil), or every plate shadows ITSELF: the wedges are far
	// wider than the offset, so the offset sample lands back on the same plate
	// almost everywhere and the whole annulus comes out 40% down. That is what
	// turns lit plates into dark bars, which is the opposite of the object.
	// Lxy rather than L.xy: COIL_LIFT is documented as a shadow LENGTH, and
	// stepping along a vector 0.71 long walks 0.71 of it.
	float thrown = coilAt(p + Lxy * COIL_LIFT, uCoilSpin) * (1.0 - coil) * ball;
	acc *= 1.0 - 0.42 * thrown;

	// The housing SUBTRACTS. A recess that adds light is not a recess, and
	// without one the plates have nothing to sit in: they float in the colour
	// field at no depth at all, which is what they used to do.
	float housing = smoothstep(HOUSING_INNER - 0.025, HOUSING_INNER + 0.012, q)
	              * smoothstep(HOUSING_OUTER + 0.025, HOUSING_OUTER - 0.012, q);
	// Deeper where the sphere turns away from the light — the occlusion the
	// phone's Canvas has to fake with a flat gradient and this gets from the
	// real normal for nothing.
	float sink = housing * ball * (0.72 + 0.28 * (1.0 - diff));
	acc = mix(acc, HOUSING, 0.74 * sink);
	alpha += sink * 0.18;

	// Ambient occlusion inside the groove: darkest hard against the walls,
	// where least of the sky reaches, and darker against the inner wall than
	// the outer because the core assembly stands over it.
	float groove = clamp(
		smoothstep(HOUSING_INNER + 0.10, HOUSING_INNER, q) * 0.60 +
		smoothstep(HOUSING_OUTER - 0.10, HOUSING_OUTER, q) * 0.42,
		0.0, 1.0);
	acc *= 1.0 - 0.45 * groove * housing * ball;

	// ...and the one wall of the two that the light can actually reach. The
	// inner wall faces outward and the outer one faces in, so the key strikes
	// exactly one of them at any given angle. This is the tell that the groove
	// has depth rather than being a dark ring painted on.
	float radialL = dot(radial, Lxy);
	float lipLight = clamp(radialL, 0.0, 1.0) * ring(q, HOUSING_INNER, 0.05)
	               + clamp(-radialL, 0.0, 1.0) * ring(q, HOUSING_OUTER, 0.05);
	acc += HUB_METAL * lipLight * ball * 0.13;
	alpha += lipLight * ball * 0.10;

	// The metal hub, between the core's dark gap and the inner seat. Struck by
	// the light rather than emitting, so it reads as a turned ring and not as a
	// fourth glowing circle.
	float hub = ring(q, HUB_FACTOR, 0.024) * ball;
	acc += HUB_METAL * hub * (0.22 + 0.78 * diff) * 0.70;
	acc += vec3(1.0) * hub * pow(ndh, 44.0) * 0.45;
	alpha += hub * 0.55;

	float across = clamp((q - SPOKE_INNER) / (SPOKE_OUTER - SPOKE_INNER), 0.0, 1.0);

	// Across the plate's own THICKNESS, bright along the inner edge where the
	// core lights it. A band stroked at one radius has no thickness to shade
	// across, which is half of why the old coils read flat. Taken too far down
	// at the outer end and the plates stop being plates and become the dark
	// space between the core and the rim.
	float face = mix(1.0, 0.42, across);
	// ...the shadow the outer seat's lip casts back down them. An unlit band
	// right under the ring is what says the plates sit BELOW it.
	face *= 1.0 - 0.55 * smoothstep(1.0 - SEAT_SHADOW_SPAN, 1.0, across);
	// ...and where the plate sits AROUND THE RING relative to the light. Ten
	// plates all at one brightness is a cog; the ones under the key have to be
	// clearly brighter than the ones opposite, or the assembly stays flat
	// however deep the recess it sits in.
	//
	// This is the one term that is stated identically on both surfaces, because
	// which plate is brightest is the most legible thing about the assembly and
	// there is no excuse for the two disagreeing about it. radial is the plate's
	// own outward direction, Lxy is the light flattened and normalised — so this
	// is exactly ReactorOrb's per-plate
	//
	//     PLATE_LIGHT_BASE + PLATE_LIGHT_GAIN * max(0, cos(plate - light))
	//
	// evaluated per pixel instead of once per wedge. It was 0.20 + 0.80 * wrap,
	// which is a different rule off a different cosine — the sphere normal
	// rather than the ring's azimuth — and it bottomed out at 0.44 where the
	// phone bottoms out at 0.50. How far the plate has turned away from the EYE
	// is a separate question and is answered separately, by the z term on the
	// line below.
	face *= PLATE_LIGHT_BASE + PLATE_LIGHT_GAIN * clamp(radialL, 0.0, 1.0);
	vec3 plateCol = mix(mix(coreCol, vec3(1.0), 0.25), rimCol, across);
	acc += plateCol * coil * face * 0.80 * (0.70 + 0.30 * lvl) * (0.55 + 0.45 * z);
	// The core's own throw across them, falling off with distance from it, so
	// the assembly is lit from inside as well as from the key outside.
	acc += coreCol * coil * (0.55 + 0.45 * lvl) / (1.0 + 5.0 * across * across) * 0.20;
	// Machined metal under glass takes a hard, narrow highlight.
	acc += mix(vec3(1.0), coreCol, 0.4) * coil * pow(ndh, 60.0) * 0.38;

	// A divider down the middle of each gap. Deliberately fainter than the
	// plates: lead with the dividers and the reactor reads as a starburst,
	// which is a different object and a much cheaper-looking one.
	float segSpan = 360.0 / SPOKE_COUNT;
	float coilBand = smoothstep(SPOKE_INNER - 0.012, SPOKE_INNER + 0.008, q)
	               * smoothstep(SPOKE_OUTER + 0.012, SPOKE_OUTER - 0.008, q);
	float segDeg = (fract(ang * INV2PI * SPOKE_COUNT - uCoilSpin * INV2PI) - 0.5) * segSpan;
	float divider = coilBand * smoothstep(1.3, 0.0, abs(abs(segDeg) - segSpan * 0.5));
	acc += rimCol * divider * 0.28 * (0.45 + 0.55 * wrap);

	// The two seat rings, over both the plates and that shadow, so the lips stay
	// bright. These are the edges of the recess, and what make the annulus read
	// as an assembly with an inside and an outside rather than as ten
	// highlights floating in the glow.
	float seat = ring(q, SPOKE_INNER, 0.016) + ring(q, SPOKE_OUTER, 0.016);
	acc += mix(rimCol, coreCol, 0.35) * seat * ball * (0.40 + 0.60 * diff) * 0.85;
	alpha += (coil * face * 0.28 + divider * 0.20 + seat * 0.40) * ball;

	// The hot centre, where the microphone level lives. Growing this rather
	// than the whole assembly is what makes speech visible without pushing the
	// outer rings off the edge.
	//
	// Kept deliberately small and short of white. A core bright enough to clip
	// is a core that erases the drifting colour field and the inner half of the
	// coils — which is most of what there is to look at.
	float coreR = max(CORE_FRACTION + CORE_LEVEL_GAIN * lvl, 1e-3);
	// Tight enough to be a source. Spread across a third of the ball it stops
	// being the hot centre of something and becomes the ball's own colour, and
	// then there is nothing for the coils to be lit BY.
	float core = smoothstep(coreR, coreR * 0.28, q);
	acc += mix(coreCol, vec3(1.0), 0.15) * core * 0.34;
	alpha += core * 0.9;

	// ---- the glass over it ------------------------------------------------
	// A self-luminous sphere has no terminator, so the last of the depth comes
	// from the cover. Two lobes off the same key: one tight enough to be a
	// point, one wide enough to be the sheen around it. Both gated by n.l, or
	// Blinn-Phong will happily put a highlight on the unlit side.
	float specMask = ball * clamp(ndl * 3.0 + 0.15, 0.0, 1.0);
	float specTight = pow(ndh, 96.0);
	float specWide = pow(ndh, 16.0);
	// ...and under both of them, the dome itself: a broad soft sheen lying over
	// the WHOLE face, coils included. The glass is in front of the mechanism as
	// well as in front of the colour, and a cover that only catches light on
	// the parts it feels like is not a cover. This wide term is doing more work
	// than either lobe above — it is the one that says there is a curved
	// surface between you and the assembly.
	float dome = pow(ndh, 6.0);
	// Only the tight lobe is white. The wide ones cover a large part of the
	// face, and white over a large part of the face is how a coloured orb turns
	// into a grey one — they carry the state's own hue instead.
	// The dome MULTIPLIES. It covers most of the face, and a broad pale term
	// laid over most of the face — added or screened, it makes no difference —
	// lifts the channels the state's colour is quietest in by more, in relative
	// terms, than the ones it is loudest in. That is desaturation, and it turns
	// a cyan orb grey and its core into a flat white disc. A gain scales all
	// three channels together: same curvature gradient across the sphere, hue
	// exactly preserved, and the core stays the colour it is supposed to be.
	acc *= 1.0 + 0.45 * dome * specMask;
	// Then the reflection proper, on top, where it is small enough to afford
	// being white.
	acc += vec3(1.0) * specTight * specMask * 1.05;
	acc += mix(vec3(1.0), rimCol, 0.40) * specWide * specMask * 0.18;
	alpha += (specTight * 0.45 + specWide * 0.12 + dome * 0.10) * ball;

	// The fill's own small catchlight, low and to the right. Two catchlights is
	// what a real glass ball on a desk has, and one is what a drawing of one
	// has.
	float specFill = pow(clamp(dot(n, Hf), 0.0, 1.0), 46.0);
	acc += mix(vec3(1.0), rimCol, 0.45) * specFill * ball * 0.22;
	alpha += specFill * ball * 0.15;

	// Fresnel. Against a field that now falls away toward the limb, the edge
	// comes up BRIGHTER than the surface just inboard of it, which is the whole
	// point of the term: it is what a rounded transparent thing always does and
	// what a flat stroked circle never does.
	acc += mix(rimCol, coreCol, 0.20) * (fresBroad * 0.38 + fresTight * 1.00) * ball;
	// A back rim on the limb opposite the key — separation from the ground.
	float back = pow(grazing, 2.2) * clamp(dot(n.xy, -Lxy), 0.0, 1.0);
	acc += rimCol * back * ball * 0.42;

	// The rolled inner edge of the cover: a narrow groove the sheen dies in,
	// held INBOARD of the fresnel band so the limb still comes up last.
	acc *= 1.0 - 0.34 * ball * smoothstep(0.80, 0.94, q) * smoothstep(1.0, 0.94, q);

	// The ball's own edge. Dim where the highlight is, bright directly
	// opposite: the same fresnel, stated on the stroke so the outline is never
	// a flat drawn circle.
	float rim = ring(r, R, 0.006 + 0.004 * lvl);
	float lit = 0.30 + 0.70 * clamp(dot(radial, -Lxy) * 0.5 + 0.5, 0.0, 1.0);
	acc += mix(rimCol, coreCol, 0.35) * rim * lit * 1.1;
	alpha += rim * 0.75;

	// ---- instrument chrome, outside the ball -------------------------------
	float spinRad = uSpin;

	// bright inner rim
	float inr = ring(r, R * INNER_RIM_FACTOR, 0.007);
	acc += rimCol * inr * 1.15; alpha += inr * 0.8;

	// rotating dashed mid ring
	float dash = smoothstep(0.5, 0.95, 0.5 + 0.5 * sin((ang + spinRad) * 28.0));
	float mid = ring(r, R * MID_DASH_FACTOR, 0.012) * dash;
	acc += rimCol * mid * 1.2; alpha += mid * 0.8;

	// Counter-rotating fine dashes. The 92 is uSpin's coefficient once the ring
	// count is folded in, and it is an INTEGER on purpose: at 1.43 * 64 = 91.52
	// a TAU wrap of uSpin slid this ring by 0.52 of a turn, which is a hard jump
	// of half the dash pattern every wrap. 92 is the same rate to within half a
	// percent and comes back to exactly where it started.
	float dash2 = smoothstep(0.6, 0.96, 0.5 + 0.5 * sin(ang * 64.0 - spinRad * 92.0));
	float mid2 = ring(r, R * FINE_DASH_FACTOR, 0.006) * dash2;
	acc += rimCol * mid2 * 0.85; alpha += mid2 * 0.6;

	// gauge tick ring (minor), then the twelve major ticks
	float gIn = R * (GAUGE_FACTOR - MINOR_TICK * 0.5);
	float gOut = R * (GAUGE_FACTOR + MINOR_TICK * 0.5);
	float band = smoothstep(gIn - 0.004, gIn, r) * smoothstep(gOut + 0.004, gOut, r);
	float tk = smoothstep(0.02, 0.004, tickDist(ang, 72.0));
	acc += rimCol * band * tk; alpha += band * tk * 0.8;

	float mIn = R * (GAUGE_FACTOR - MAJOR_TICK * 0.5);
	float mOut = R * (GAUGE_FACTOR + MAJOR_TICK * 0.5);
	float band2 = smoothstep(mIn - 0.004, mIn, r) * smoothstep(mOut + 0.004, mOut, r);
	float tk2 = smoothstep(0.014, 0.002, tickDist(ang, 12.0));
	acc += rimCol * band2 * tk2 * 1.25; alpha += band2 * tk2 * 0.9;

	// radar sweep in the annulus between the ball and the gauge
	float sweepAng = mod(-spinRad + PI, TAU) - PI;
	float da = mod(ang - sweepAng + PI, TAU) - PI;
	float trail = smoothstep(1.3, 0.0, da) * step(0.0, da);
	float sweepMask = smoothstep(R * GAUGE_FACTOR, R * SWEEP_INNER_FACTOR, r)
	                * step(R * SWEEP_INNER_FACTOR, r);
	acc += rimCol * trail * sweepMask * (0.20 + 0.22 * lvl);
	alpha += trail * sweepMask * 0.14;

	// thinking turbulence
	if (thinking > 0.5) {
		float sw = 0.5 + 0.5 * sin(ang * 3.0 + uTime * 3.0) * sin(r * 14.0 - uTime * 4.0);
		float tt = ring(r, R * TURBULENCE_FACTOR * (1.0 + 0.02 * sin(uBreath * 2.0)), 0.02) * sw;
		acc += rimCol * tt * 0.7; alpha += tt * 0.4;
	}

	// outer boundary ring + halo
	float outer = ring(r, R * OUTER_FACTOR, 0.004);
	acc += rimCol * outer * 0.55; alpha += outer * 0.4;
	acc += rimCol * exp(-2.6 * max(r - R * HALO_FRACTION, 0.0)) * (0.05 + 0.04 * lvl);

	// ---- bloom -------------------------------------------------------------
	// A separate pass, wider and dimmer than the thing it comes off, and NOT
	// masked by the ball — so it spills past the limb and washes over the
	// chrome. That overspill is the whole difference between light that is in
	// the air and light that is painted on the object; making the core bigger
	// or brighter instead just gives a bigger, brighter core.
	//
	// It tracks the blobs, so the glow outside the ball moves with the colour
	// inside it rather than sitting there as a static ring.
	vec3 blobAvg = (b0 + b1 + b2) * 0.3333333;
	float bloomR = R * 1.55;
	float airBlobs = (softGlow(uv, c0 * R, bloomR)
	                + softGlow(uv, c1 * R, bloomR)
	                + softGlow(uv, c2 * R, bloomR)) * 0.3333333;
	// Wider and weaker than the core it comes off. Tight and strong is just the
	// core again, drawn twice, and it lands on the one part of the face that
	// was already closest to clipping.
	float airCore = softGlow(uv, vec2(0.0), R * (0.95 + 0.25 * lvl));
	acc += blobAvg * airBlobs * (0.30 + 0.20 * lvl);
	// Pulled toward the state's own colour rather than the core's near-white,
	// so the air around the orb reads as its colour and not as fog.
	acc += mix(coreCol, rimCol, 0.45) * airCore * (0.15 + 0.12 * lvl);
	// The highlight bleeds too, or it reads as a painted dot.
	acc += vec3(1.0) * pow(ndh, 5.0) * ball * 0.06;
	alpha += (airBlobs * 0.30 + airCore * 0.26) * (0.55 + 0.45 * lvl);

	// Loud speech already grows the core, the blobs, the sweep and the bloom.
	// A big global gain on top of all four is what takes the middle past the
	// shoulder and flattens the assembly into a white disc at exactly the
	// moment there is most to look at.
	acc *= 0.86 + 0.22 * lvl;
	// A shoulder rather than a clamp. Everything under KNEE is untouched and
	// everything over it rolls off ASYMPTOTICALLY toward 1 — it approaches white
	// and never arrives, so the bright middle keeps its hue and its coils
	// instead of flattening into a white hole where they used to be. A plain
	// clamp is what puts that hole there, and with three screened blobs, a core
	// and a bloom all landing on the same few hundred pixels, the middle goes
	// well past 1 and the hole is most of the assembly.
	vec3 knee = max(acc - vec3(0.65), vec3(0.0));
	acc = min(acc, vec3(0.65)) + 0.35 * (vec3(1.0) - exp(-knee / 0.35));

	acc = clamp(acc, 0.0, 1.0);
	alpha = clamp(alpha, 0.0, 1.0);
	// Premultiplied, to match the context and the blend func. Straight alpha
	// into a transparent framebuffer multiplies the colour by alpha at the
	// blend and by alpha AGAIN when the browser composites the canvas over the
	// page, so anything faint arrives at roughly its own cube — which is a
	// death sentence for the bloom, the halo and the sweep, the three things
	// that are supposed to look like light in the air.
	gl_FragColor = vec4(acc * alpha, alpha);
}
`;

	onMount(() => {
		if (!canvas) return;
		const gl = canvas.getContext('webgl', { alpha: true, antialias: true, premultipliedAlpha: true });
		if (!gl) {
			webglOk = false;
			return;
		}

		const compile = (type: number, src: string) => {
			const sh = gl.createShader(type)!;
			gl.shaderSource(sh, src);
			gl.compileShader(sh);
			if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
				console.error('shader error:', gl.getShaderInfoLog(sh));
				throw new Error('shader compile failed');
			}
			return sh;
		};

		let program: WebGLProgram;
		try {
			program = gl.createProgram()!;
			gl.attachShader(program, compile(gl.VERTEX_SHADER, VERT));
			gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAG));
			gl.linkProgram(program);
			if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('link failed');
		} catch {
			webglOk = false;
			return;
		}
		gl.useProgram(program);

		const quad = gl.createBuffer();
		gl.bindBuffer(gl.ARRAY_BUFFER, quad);
		gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
		const aPos = gl.getAttribLocation(program, 'aPos');
		gl.enableVertexAttribArray(aPos);
		gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

		const uRes = gl.getUniformLocation(program, 'uRes');
		const uTime = gl.getUniformLocation(program, 'uTime');
		const uLevel = gl.getUniformLocation(program, 'uLevel');
		const uState = gl.getUniformLocation(program, 'uState');
		const uPhases = gl.getUniformLocation(program, 'uPhases');
		const uSpin = gl.getUniformLocation(program, 'uSpin');
		const uCoilSpin = gl.getUniformLocation(program, 'uCoilSpin');
		const uBreath = gl.getUniformLocation(program, 'uBreath');
		const uDrift = gl.getUniformLocation(program, 'uDrift');

		gl.enable(gl.BLEND);
		// Premultiplied source, matching both the context flag above and the
		// shader's final line. gl.SRC_ALPHA here would apply alpha a second time.
		gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);

		let raf = 0;
		/*
		 * THE MOTION KILL SWITCH, for the one thing CSS cannot reach.
		 *
		 * `base.css` cuts every animation and transition in the app to nothing
		 * under `prefers-reduced-motion: reduce`, and it cannot touch this: a
		 * requestAnimationFrame loop is not an animation as far as the cascade is
		 * concerned. So the largest, brightest, most continuously moving object on
		 * the screen — three drifting blobs, two counter-rotating rings, a radar
		 * sweep and a breathing core — was the single thing that ignored the
		 * setting entirely.
		 *
		 * Reduced does not mean blank. The orb is still drawn, in full, at its
		 * resting phase; what stops is time. `watchReducedMotion` is why the
		 * preference is honoured when it changes rather than only at load — a
		 * system-wide toggle should not need a reload to take effect.
		 */
		let reduced = prefersReducedMotion();
		const start = performance.now();
		let last = start;
		// Free-running, integrated against the wall clock. Deriving these from
		// uTime instead would jump the phase by t * delta every time a state
		// change moved the rate — several full turns, mid-animation.
		const phases = [0, 0, 0];
		let spin = 0;
		let coilSpin = 0;
		let breath = 0;
		let drift = 0;

		const draw = () => {
			raf = 0;
			const nowMs = performance.now();
			// A tab that was in the background hands back one enormous dt; a
			// clamp turns that into a dropped frame instead of a jump. Zero when
			// motion is off: every phase below is integrated from it, so one number
			// stops the drift, the spin, the sweep and the breath together.
			const dt = reduced ? 0 : Math.min((nowMs - last) / 1000, 0.1);
			last = nowMs;

			const dpr = Math.min(window.devicePixelRatio || 1, 2);
			const w = Math.round(canvas!.clientWidth * dpr);
			const h = Math.round(canvas!.clientHeight * dpr);
			if (canvas!.width !== w || canvas!.height !== h) {
				canvas!.width = w;
				canvas!.height = h;
			}
			gl.viewport(0, 0, w, h);
			gl.clearColor(0, 0, 0, 0);
			gl.clear(gl.COLOR_BUFFER_BIT);

			const target = STATE_NUM[orbState] ?? 0;
			if (reduced) {
				// Snapped rather than eased, for the same reason the CSS kill switch
				// sets a duration of nothing: the end state is identical, it simply
				// arrives at once. Easing over frames that are not coming would leave
				// the colour a fifth of the way to the state it is reporting.
				smoothLevel = Math.min(level, 1);
				smoothState = target;
			} else {
				smoothLevel += (Math.min(level, 1) - smoothLevel) * 0.22;
				smoothState += (target - smoothState) * 0.15;
			}

			const hz = mix4(ORBIT_HZ, smoothState) * (1 + 0.6 * smoothLevel);
			for (let i = 0; i < 3; i++) {
				phases[i] = (phases[i] + dt * hz * BLOB_RATE[i] * TAU) % TAU;
			}
			// 0.35 rad/s at rest, 0.70 while a turn is live — the 20/40 degrees a
			// second the phone turns its chrome at.
			const spinRate = 0.35 + (smoothState > 0.5 ? 0.35 : 0.0);
			spin = (spin + dt * spinRate) % TAU;
			// The same rotation, counted in pattern turns so its own TAU wrap lands
			// on a whole segment.
			coilSpin = (coilSpin + dt * spinRate * SPOKE_SPIN_RATIO * SPOKE_COUNT) % TAU;
			breath = (breath + (dt * TAU) / mix4(BREATH_S, smoothState)) % TAU;
			drift = (drift + (dt * TAU) / DRIFT_S) % TAU;

			gl.uniform2f(uRes, w, h);
			gl.uniform1f(uTime, (nowMs - start) / 1000);
			gl.uniform1f(uLevel, smoothLevel);
			gl.uniform1f(uState, smoothState);
			gl.uniform3f(uPhases, phases[0], phases[1], phases[2]);
			gl.uniform1f(uSpin, spin);
			gl.uniform1f(uCoilSpin, coilSpin);
			gl.uniform1f(uBreath, breath);
			gl.uniform1f(uDrift, drift);
			gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
			if (!reduced) raf = requestAnimationFrame(draw);
		};

		/** Ask for a frame, unless one is already coming. */
		const schedule = () => {
			if (!raf) raf = requestAnimationFrame(draw);
		};

		const unwatch = watchReducedMotion((now) => {
			reduced = now;
			// The clock has been standing still while paused, and `last` with it —
			// without this the first frame back integrates the whole pause into one
			// step. The dt clamp caps that at a tenth of a second, which is still a
			// tenth of a second of movement arriving in one frame.
			last = performance.now();
			schedule();
		});
		redraw = schedule;
		schedule();
		return () => {
			if (raf) cancelAnimationFrame(raf);
			raf = 0;
			redraw = null;
			unwatch();
		};
	});
</script>

{#if webglOk}
	<canvas bind:this={canvas} class="orb" data-testid="orb" data-state={orbState}></canvas>
{:else}
	<div
		class="orb orb-fallback {orbState}"
		data-testid="orb"
		data-state={orbState}
		style="--lvl: {Math.min(level, 1)}"
	></div>
{/if}

<style>
	.orb {
		width: 100%;
		height: 100%;
		display: block;
	}
	/*
	 * No-WebGL fallback. One shape, one colour variable per state, mixed against
	 * the page ground — the shader above has its own copy of these four colours
	 * because GLSL cannot read a custom property, but nothing in CSS should.
	 *
	 * Same light as the shader's: a tight off-centre catchlight up and to the
	 * left, a dim fill down and to the right, a bright limb, and a shadowed
	 * lower edge. It is the same ball with fewer terms, not a flat disc.
	 */
	.orb-fallback {
		--orb: var(--jv-accent-deep);
		width: 100%;
		height: 100%;
		border-radius: 50%;
		background:
			radial-gradient(circle at 33% 29%, rgba(255, 255, 255, 0.5) 0%, transparent 16%),
			radial-gradient(circle at 33% 29%, rgba(255, 255, 255, 0.22) 0%, transparent 38%),
			radial-gradient(circle at 70% 76%, rgba(255, 255, 255, 0.1) 0%, transparent 26%),
			radial-gradient(
				circle at 50% 50%,
				color-mix(in srgb, var(--orb) 30%, transparent) 62%,
				color-mix(in srgb, var(--orb) 85%, transparent) 88%,
				transparent 94%
			),
			radial-gradient(
				circle at 40% 36%,
				color-mix(in srgb, var(--orb) 92%, var(--jv-bg)) 0%,
				color-mix(in srgb, var(--orb) 34%, var(--jv-bg)) 58%,
				color-mix(in srgb, var(--orb) 8%, var(--jv-bg)) 76%,
				transparent 80%
			);
		box-shadow:
			0 0 60px 10px color-mix(in srgb, var(--orb) 26%, transparent),
			inset 0 -10px 24px -8px rgba(0, 0, 0, 0.7);
		transform: scale(calc(1 + var(--lvl, 0) * 0.15));
		animation: breathe 3.5s ease-in-out infinite;
	}
	.orb-fallback.listening {
		--orb: var(--jv-accent);
		box-shadow:
			0 0 80px 16px color-mix(in srgb, var(--orb) 45%, transparent),
			inset 0 -10px 24px -8px rgba(0, 0, 0, 0.7);
		animation-duration: 1.4s;
	}
	.orb-fallback.thinking {
		--orb: var(--jv-amber);
		box-shadow:
			0 0 80px 16px color-mix(in srgb, var(--orb) 40%, transparent),
			inset 0 -10px 24px -8px rgba(0, 0, 0, 0.7);
		animation-duration: 1s;
	}
	.orb-fallback.speaking {
		--orb: var(--jv-gold);
		box-shadow:
			0 0 90px 18px color-mix(in srgb, var(--orb) 45%, transparent),
			inset 0 -10px 24px -8px rgba(0, 0, 0, 0.7);
		animation-duration: 1.2s;
	}
	@keyframes breathe {
		0%,
		100% {
			filter: brightness(0.85);
		}
		50% {
			filter: brightness(1.15);
		}
	}
</style>
