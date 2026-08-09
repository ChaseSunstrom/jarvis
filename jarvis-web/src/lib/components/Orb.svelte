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

	const VERT = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`;

	// Jarvis-style reactor: arc core + concentric rings, rotating dashed rings,
	// a gauge tick ring, and a radar sweep. Single pass, 60fps-friendly.
	const FRAG = `
precision mediump float;
uniform vec2 uRes;
uniform float uTime;
uniform float uLevel;   // 0..1 audio energy
uniform float uState;   // 0 idle, 1 listen, 2 think, 3 speak

const float PI = 3.14159265;
const float INV2PI = 0.15915494;

vec3 stateColor(float s) {
	vec3 idle   = vec3(0.16, 0.68, 0.88);
	vec3 listen = vec3(0.26, 0.86, 1.00);
	vec3 think  = vec3(1.00, 0.60, 0.16);
	vec3 speak  = vec3(1.00, 0.80, 0.34);
	if (s < 0.5) return mix(idle, listen, s / 0.5);
	if (s < 1.5) return mix(listen, listen, 0.0);
	if (s < 2.5) return mix(listen, think, (s - 1.5));
	return mix(think, speak, clamp(s - 2.5, 0.0, 1.0));
}

float ring(float r, float rr, float th) { return smoothstep(th, 0.0, abs(r - rr)); }

float tickDist(float ang, float count) {
	float a = ang * INV2PI + 0.5;
	float f = fract(a * count);
	return min(f, 1.0 - f);
}

void main() {
	vec2 uv = (gl_FragCoord.xy * 2.0 - uRes) / min(uRes.x, uRes.y);
	float r = length(uv);
	float ang = atan(uv.y, uv.x);
	float t = uTime;
	float lvl = clamp(uLevel, 0.0, 1.0);
	vec3 col = stateColor(uState);
	float thinking = step(1.5, uState) * step(uState, 2.5);
	float active = step(0.5, uState); // listening/thinking/speaking spin faster

	vec3 acc = vec3(0.0);
	float alpha = 0.0;

	// --- arc-reactor core ---
	float coreR = 0.125 + lvl * 0.05;
	float core = smoothstep(coreR, coreR - 0.05, r);
	float coreGlow = exp(-6.5 * max(r - coreR, 0.0));
	float pulse = 0.5 + 0.5 * sin(t * (uState < 0.5 ? 1.6 : 4.2));
	acc += col * (core * 1.55 + coreGlow * (0.7 + 0.4 * pulse));
	acc += vec3(1.0) * core * 0.25; // hot white centre
	alpha += core + coreGlow * 0.75;

	// bright inner rim
	float inr = ring(r, 0.185, 0.007);
	acc += col * inr * 1.15; alpha += inr * 0.8;

	// rotating dashed mid ring
	float rot = t * (0.35 + active * 0.35);
	float dash = smoothstep(0.5, 0.95, 0.5 + 0.5 * sin((ang + rot) * 28.0));
	float mid = ring(r, 0.275, 0.012) * dash;
	acc += col * mid * 1.2; alpha += mid * 0.8;

	// counter-rotating fine dashes
	float dash2 = smoothstep(0.6, 0.96, 0.5 + 0.5 * sin((ang - t * (0.5 + active * 0.5)) * 64.0));
	float mid2 = ring(r, 0.325, 0.006) * dash2;
	acc += col * mid2 * 0.85; alpha += mid2 * 0.6;

	// gauge tick ring (minor)
	float band = smoothstep(0.378, 0.383, r) * smoothstep(0.412, 0.407, r);
	float td = tickDist(ang, 72.0);
	float tk = smoothstep(0.02, 0.004, td);
	acc += col * band * tk; alpha += band * tk * 0.8;

	// major ticks (longer, brighter)
	float band2 = smoothstep(0.372, 0.377, r) * smoothstep(0.424, 0.418, r);
	float td2 = tickDist(ang, 12.0);
	float tk2 = smoothstep(0.014, 0.002, td2);
	acc += col * band2 * tk2 * 1.25; alpha += band2 * tk2 * 0.9;

	// radar sweep between core and gauge
	float sweepAng = mod(-t * 0.7 + PI, 2.0 * PI) - PI;
	float da = mod(ang - sweepAng + PI, 2.0 * PI) - PI;
	float trail = smoothstep(1.3, 0.0, da) * step(0.0, da);
	float sweepMask = smoothstep(0.36, 0.19, r) * step(0.19, r);
	acc += col * trail * sweepMask * (0.20 + 0.22 * lvl);
	alpha += trail * sweepMask * 0.14;

	// thinking turbulence
	if (thinking > 0.5) {
		float sw = 0.5 + 0.5 * sin(ang * 3.0 + t * 3.0) * sin(r * 14.0 - t * 4.0);
		float tt = ring(r, 0.235 + 0.02 * sin(t * 2.0), 0.02) * sw;
		acc += col * tt * 0.7; alpha += tt * 0.4;
	}

	// outer boundary ring + halo
	float outer = ring(r, 0.46, 0.004);
	acc += col * outer * 0.55; alpha += outer * 0.4;
	acc += col * exp(-2.6 * max(r - 0.46, 0.0)) * 0.09;

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

		gl.enable(gl.BLEND);
		gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

		let raf = 0;
		const start = performance.now();
		const draw = () => {
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
			gl.uniform2f(uRes, w, h);
			gl.uniform1f(uTime, (performance.now() - start) / 1000);
			gl.uniform1f(uLevel, smoothLevel);
			gl.uniform1f(uState, smoothState);
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
	 */
	.orb-fallback {
		--orb: var(--jv-accent-deep);
		width: 100%;
		height: 100%;
		border-radius: 50%;
		background: radial-gradient(
			circle at 50% 45%,
			color-mix(in srgb, var(--orb) 84%, var(--jv-bg)) 0%,
			color-mix(in srgb, var(--orb) 26%, var(--jv-bg)) 57%,
			transparent 73%
		);
		box-shadow: 0 0 60px 10px color-mix(in srgb, var(--orb) 26%, transparent);
		transform: scale(calc(1 + var(--lvl, 0) * 0.15));
		animation: breathe 3.5s ease-in-out infinite;
	}
	.orb-fallback.listening {
		--orb: var(--jv-accent);
		box-shadow: 0 0 80px 16px color-mix(in srgb, var(--orb) 45%, transparent);
		animation-duration: 1.4s;
	}
	.orb-fallback.thinking {
		--orb: var(--jv-amber);
		box-shadow: 0 0 80px 16px color-mix(in srgb, var(--orb) 40%, transparent);
		animation-duration: 1s;
	}
	.orb-fallback.speaking {
		--orb: var(--jv-gold);
		box-shadow: 0 0 90px 18px color-mix(in srgb, var(--orb) 45%, transparent);
		animation-duration: 1.2s;
	}
	@keyframes breathe {
		0%, 100% { filter: brightness(0.85); }
		50% { filter: brightness(1.15); }
	}
</style>
