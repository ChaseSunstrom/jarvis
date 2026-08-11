<script lang="ts">
	import { onMount } from 'svelte';

	let {
		level = 0,
		orbState = 'idle'
	}: { level?: number; orbState?: 'idle' | 'listening' | 'thinking' | 'speaking' } = $props();

	const STATE_NUM: Record<string, number> = { idle: 0, listening: 1, thinking: 2, speaking: 3 };

	let canvas: HTMLCanvasElement | undefined = $state();
	let webglOk = $state(true);
	let smoothLevel = 0;
	let smoothState = 0;

	const TAU = Math.PI * 2;

	/**
	 * Blob-drift rate per state, turns per second. The same table the phone's
	 * `SiriPalette.orbitHz` holds — 3.5s at rest down to 1s while thinking — so
	 * the browser and the phone show one object moving at one speed.
	 */
	const ORBIT_HZ = [1 / 3.5, 1 / 1.4, 1 / 1.0, 1 / 1.2];

	/** Breathing period per state, seconds. Also the phone's. */
	const BREATH_S = [3.5, 1.4, 1.0, 1.2];

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
	 * The arc reactor, as one object.
	 *
	 * This is the same reactor the Android app draws (`ReactorOrb.kt`), with the
	 * same proportions, the same palette and the same rates — the two are pinned
	 * against each other by `android-app/tools/reactor_orb_test.py`. Three things
	 * happen here that a Canvas cannot do as cheaply, and they are the whole of
	 * "make it look 3D":
	 *
	 *   * a real sphere normal, so the drifting colour field is brightest where
	 *     the ball faces you and falls away toward the limb;
	 *   * a Blinn-Phong highlight off a glass cover, fixing a light source up and
	 *     to the left;
	 *   * a fresnel limb, which is what a rounded transparent object always does
	 *     and what a flat stroked circle never does.
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
precision mediump float;
uniform vec2 uRes;
uniform float uTime;
uniform float uLevel;   // 0..1 audio energy, already gained
uniform float uState;   // 0 idle, 1 listen, 2 think, 3 speak (fractional while blending)
uniform float uPhase;   // blob orbit, radians, free-running
uniform float uSpin;    // chrome rotation, radians, free-running
uniform float uBreath;  // breathing, radians, free-running

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
const float SPOKE_GAP_DEG = 9.0;
const float SPOKE_SPIN_RATIO = 0.35;
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
	return smoothstep(1.0, 0.0, length(p - centre) / radius);
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
	vec2 uv = (gl_FragCoord.xy * 2.0 - uRes) / min(uRes.x, uRes.y);
	float r = length(uv);
	float ang = atan(uv.y, uv.x);
	float lvl = clamp(uLevel, 0.0, 1.0);
	float thinking = step(1.5, uState) * step(uState, 2.5);

	vec3 b0, b1, b2, coreCol;
	palette(uState, b0, b1, b2, coreCol);
	vec3 rimCol = b0;

	// The ball breathes and swells with the voice. Everything else is a
	// multiple of it, and OUTER_FACTOR * BALL stays inside the viewport.
	float breath = 1.0 + 0.04 * sin(uBreath);
	float R = BALL * breath * (1.0 + 0.06 * lvl);
	float q = r / R;                       // 0 at the centre, 1 at the ball's edge
	vec2 p = uv / R;                       // ball-space, for the blobs

	vec3 acc = vec3(0.0);
	float alpha = 0.0;

	// ---- the ball ---------------------------------------------------------
	float ball = smoothstep(1.0, 0.93, q);
	// The sphere. z is the surface normal's component toward the viewer, which
	// is what turns a flat disc of colour into something with a near side.
	float z = sqrt(max(1.0 - q * q, 0.0));
	vec3 n = vec3(p.x, p.y, z + 1e-4);
	n = normalize(n);

	// The dark ground the colours are lit against. Additive blending needs
	// something to add TO.
	acc += SUBSTRATE * ball;
	alpha += ball * 0.90;

	// Three drifting blobs. Rates 1 : 0.73 : 1.31 never return to the same
	// arrangement, so the field does not visibly loop.
	float orbit = ORBIT_FRACTION * (0.75 + 0.25 * lvl);
	float br = BLOB_FRACTION * (1.0 + 0.10 * lvl);
	vec2 c0 = vec2(cos(uPhase * 1.00), sin(uPhase * 1.00) * 0.72) * orbit;
	vec2 c1 = vec2(cos(uPhase * 0.73 + 2.0943951), sin(uPhase * 0.73 + 2.0943951) * 0.72) * orbit;
	vec2 c2 = vec2(cos(uPhase * 1.31 + 4.1887902), sin(uPhase * 1.31 + 4.1887902) * 0.72) * orbit;
	vec3 field = screen(
		screen(b0 * blob(p, c0, br), b1 * blob(p, c1, br)),
		b2 * blob(p, c2, br)
	);
	// Brightest where the ball faces you, falling off toward the limb.
	acc += field * ball * (0.34 + 0.52 * z);
	alpha += ball * 0.10;

	// The coils: ten plates in an annulus, turning against the chrome. The
	// single element that most says "arc reactor" rather than "orb", and the
	// blob field lights them rather than being covered by them.
	float coilBand = smoothstep(SPOKE_INNER - 0.03, SPOKE_INNER + 0.02, q)
	               * smoothstep(SPOKE_OUTER + 0.03, SPOKE_OUTER - 0.02, q);
	float seg = fract((ang * INV2PI - uSpin * INV2PI * SPOKE_SPIN_RATIO) * SPOKE_COUNT);
	float gapFrac = SPOKE_GAP_DEG / (360.0 / SPOKE_COUNT);
	float coil = coilBand * smoothstep(0.0, 0.03, seg) * smoothstep(1.0 - gapFrac + 0.03, 1.0 - gapFrac - 0.03, seg);
	acc += mix(rimCol, coreCol, 0.6) * coil * 0.42 * (0.70 + 0.30 * lvl) * (0.5 + 0.5 * z);
	// A divider down the middle of each gap. Deliberately fainter than the
	// plates: lead with the dividers and the reactor reads as a starburst,
	// which is a different object and a much cheaper-looking one.
	float divider = coilBand * smoothstep(0.03, 0.0, abs(seg - (1.0 - gapFrac * 0.5)));
	acc += rimCol * divider * 0.28;
	// The two rings the plates are seated between. These are what make the
	// annulus read as an assembly with an inside and an outside rather than as
	// ten highlights floating in the glow.
	float seat = ring(q, SPOKE_INNER, 0.018) + ring(q, SPOKE_OUTER, 0.018);
	acc += mix(rimCol, coreCol, 0.35) * seat * ball * 0.55;
	alpha += (coil * 0.20 + divider * 0.20 + seat * 0.35) * ball;

	// The hot centre, where the microphone level lives. Growing this rather
	// than the whole assembly is what makes speech visible without pushing the
	// outer rings off the edge.
	//
	// Kept deliberately small and short of white. A core bright enough to clip
	// is a core that erases the drifting colour field and the inner half of the
	// coils — which is most of what there is to look at.
	float coreR = CORE_FRACTION + CORE_LEVEL_GAIN * lvl;
	float core = smoothstep(coreR, coreR * 0.30, q);
	acc += mix(coreCol, vec3(1.0), 0.35) * core * 0.62;
	alpha += core * 0.9;

	// ---- the glass over it ------------------------------------------------
	// A self-luminous sphere has no terminator, so the depth comes from the
	// cover: one highlight, and a limb that brightens the way every rounded
	// transparent thing's does.
	vec3 L = normalize(vec3(-0.42, 0.52, 0.74));
	vec3 H = normalize(L + vec3(0.0, 0.0, 1.0));
	float spec = pow(max(dot(n, H), 0.0), 26.0);
	acc += vec3(1.0) * spec * ball * 0.62;
	alpha += spec * ball * 0.35;

	float fres = pow(1.0 - z, 3.2);
	acc += rimCol * fres * ball * 0.55;
	// ...and the rolled inner edge the highlight falls off across.
	acc *= 1.0 - 0.45 * smoothstep(0.82, 1.0, q) * ball;

	// The ball's own edge.
	float rim = ring(r, R, 0.006 + 0.004 * lvl);
	// Dim where the highlight is, bright directly opposite: the same fresnel,
	// stated on the stroke so the outline is never a flat drawn circle.
	float lit = 0.35 + 0.65 * clamp(dot(normalize(vec2(uv.x, uv.y) + 1e-5), -L.xy) * 0.5 + 0.5, 0.0, 1.0);
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

	// counter-rotating fine dashes
	float dash2 = smoothstep(0.6, 0.96, 0.5 + 0.5 * sin((ang - spinRad * 1.43) * 64.0));
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
	acc += rimCol * exp(-2.6 * max(r - R * HALO_FRACTION, 0.0)) * (0.07 + 0.05 * lvl);

	acc *= 0.88 + 0.5 * lvl;
	alpha = clamp(alpha, 0.0, 1.0);
	gl_FragColor = vec4(acc, alpha);
}
`;

	onMount(() => {
		if (!canvas) return;
		const gl = canvas.getContext('webgl', { alpha: true, antialias: true, premultipliedAlpha: false });
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
		const uPhase = gl.getUniformLocation(program, 'uPhase');
		const uSpin = gl.getUniformLocation(program, 'uSpin');
		const uBreath = gl.getUniformLocation(program, 'uBreath');

		gl.enable(gl.BLEND);
		gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

		let raf = 0;
		const start = performance.now();
		let last = start;
		// Free-running, integrated against the wall clock. Deriving these from
		// uTime instead would jump the phase by t * delta every time a state
		// change moved the rate — several full turns, mid-animation.
		let phase = 0;
		let spin = 0;
		let breath = 0;

		const draw = () => {
			const nowMs = performance.now();
			// A tab that was in the background hands back one enormous dt; a
			// clamp turns that into a dropped frame instead of a jump.
			const dt = Math.min((nowMs - last) / 1000, 0.1);
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

			smoothLevel += (Math.min(level, 1) - smoothLevel) * 0.22;
			const target = STATE_NUM[orbState] ?? 0;
			smoothState += (target - smoothState) * 0.15;

			const hz = mix4(ORBIT_HZ, smoothState) * (1 + 0.6 * smoothLevel);
			phase = (phase + dt * hz * TAU) % TAU;
			// 0.35 rad/s at rest, 0.70 while a turn is live — the 20/40 degrees a
			// second the phone turns its chrome at.
			spin = (spin + dt * (0.35 + (smoothState > 0.5 ? 0.35 : 0.0))) % TAU;
			breath = (breath + (dt * TAU) / mix4(BREATH_S, smoothState)) % TAU;

			gl.uniform2f(uRes, w, h);
			gl.uniform1f(uTime, (nowMs - start) / 1000);
			gl.uniform1f(uLevel, smoothLevel);
			gl.uniform1f(uState, smoothState);
			gl.uniform1f(uPhase, phase);
			gl.uniform1f(uSpin, spin);
			gl.uniform1f(uBreath, breath);
			gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
			raf = requestAnimationFrame(draw);
		};
		raf = requestAnimationFrame(draw);
		return () => cancelAnimationFrame(raf);
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
	 * The off-centre highlight is the same light source the shader uses, so the
	 * fallback is a dimmer version of the same ball rather than a flat disc.
	 */
	.orb-fallback {
		--orb: var(--jv-accent-deep);
		width: 100%;
		height: 100%;
		border-radius: 50%;
		background:
			radial-gradient(circle at 33% 31%, rgba(255, 255, 255, 0.34) 0%, transparent 34%),
			radial-gradient(
				circle at 50% 50%,
				color-mix(in srgb, var(--orb) 88%, var(--jv-bg)) 0%,
				color-mix(in srgb, var(--orb) 30%, var(--jv-bg)) 55%,
				color-mix(in srgb, var(--orb) 8%, var(--jv-bg)) 72%,
				transparent 78%
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
