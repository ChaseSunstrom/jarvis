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

	const VERT = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`;

	// Modest single-pass shader: radial core + glow, per-state color and motion.
	const FRAG = `
precision mediump float;
uniform vec2 uRes;
uniform float uTime;
uniform float uLevel;
uniform float uState;

vec3 stateColor(float s) {
	vec3 idle = vec3(0.10, 0.55, 0.65);      // dim cyan
	vec3 listen = vec3(0.15, 0.85, 1.00);    // bright cyan
	vec3 think = vec3(1.00, 0.65, 0.15);     // amber
	vec3 speak = vec3(1.00, 0.82, 0.35);     // warm gold
	if (s < 0.5) return idle;
	if (s < 1.5) return listen;
	if (s < 2.5) return think;
	return speak;
}

void main() {
	vec2 uv = (gl_FragCoord.xy * 2.0 - uRes) / min(uRes.x, uRes.y);
	float r = length(uv);
	float a = atan(uv.y, uv.x);

	float breathe = 0.03 * sin(uTime * (uState < 0.5 ? 1.2 : 3.0));
	float swirl = 0.0;
	if (uState > 1.5 && uState < 2.5) {
		swirl = 0.05 * sin(a * 3.0 + uTime * 2.5) * sin(r * 9.0 - uTime * 3.0);
	}
	float radius = 0.42 + breathe + uLevel * 0.16 + swirl;

	float core = smoothstep(radius, radius - 0.06, r);
	float rim = smoothstep(0.045, 0.0, abs(r - radius));
	float glow = exp(-3.2 * max(r - radius, 0.0)) * (0.35 + uLevel * 0.65);
	float inner = exp(-4.0 * r) * (0.55 + 0.45 * sin(uTime * 1.7 + r * 12.0));

	vec3 col = stateColor(uState);
	vec3 c = col * (core * (0.28 + inner) + rim * 1.2 + glow * 0.8);
	float alpha = clamp(core + rim + glow * 0.7, 0.0, 1.0);
	gl_FragColor = vec4(c, alpha);
}
`;

	onMount(() => {
		if (!canvas) return;
		const gl = canvas.getContext('webgl', { alpha: true, antialias: true });
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
		gl.bufferData(
			gl.ARRAY_BUFFER,
			new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
			gl.STATIC_DRAW
		);
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

			smoothLevel += (Math.min(level, 1) - smoothLevel) * 0.25;
			gl.uniform2f(uRes, w, h);
			gl.uniform1f(uTime, (performance.now() - start) / 1000);
			gl.uniform1f(uLevel, smoothLevel);
			gl.uniform1f(uState, STATE_NUM[orbState] ?? 0);
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
		width: min(46vmin, 380px);
		height: min(46vmin, 380px);
		display: block;
	}
	.orb-fallback {
		border-radius: 50%;
		background: radial-gradient(circle at 50% 45%, #0e5a68 0%, #062a33 55%, transparent 72%);
		box-shadow: 0 0 60px 10px rgba(20, 180, 210, 0.25);
		transform: scale(calc(1 + var(--lvl, 0) * 0.15));
		animation: breathe 3.5s ease-in-out infinite;
	}
	.orb-fallback.listening {
		background: radial-gradient(circle at 50% 45%, #17d3ff 0%, #05364a 60%, transparent 75%);
		box-shadow: 0 0 80px 16px rgba(30, 210, 255, 0.45);
		animation-duration: 1.4s;
	}
	.orb-fallback.thinking {
		background: radial-gradient(circle at 50% 45%, #ffa626 0%, #4a2c05 60%, transparent 75%);
		box-shadow: 0 0 80px 16px rgba(255, 166, 38, 0.4);
		animation-duration: 1s;
	}
	.orb-fallback.speaking {
		background: radial-gradient(circle at 50% 45%, #ffd25e 0%, #4a3a08 60%, transparent 75%);
		box-shadow: 0 0 90px 18px rgba(255, 210, 94, 0.45);
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
