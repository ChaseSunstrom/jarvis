import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/**
 * The orb's shader has to actually compile.
 *
 * Nothing else in this repository would notice if it did not. `Orb.svelte`
 * catches every failure — no WebGL, a compile error, a link error — and quietly
 * swaps in a CSS circle, which is the right behaviour on a machine with no GPU
 * and completely wrong as a silent outcome of a typo. The page still renders,
 * `[data-testid="orb"]` is still there with the right `data-state`, and the
 * whole existing suite goes green on a reactor nobody can see.
 *
 * This has already happened once. A backtick inside a GLSL comment — the
 * shader lives in a JS template literal — truncated the source at that line;
 * the fragment shader ended mid-comment and every orb in the console became a
 * flat disc. The build was clean. So the check is: extract the two shaders the
 * component actually ships, hand them to a real WebGL context, and insist they
 * compile, link, resolve their uniforms and draw something.
 */

const SOURCE = fileURLToPath(new URL('../src/lib/components/Orb.svelte', import.meta.url));

/** Pull one backtick-delimited constant out of the component. */
function shader(name: 'VERT' | 'FRAG'): string {
	const src = readFileSync(SOURCE, 'utf8');
	const at = src.indexOf(`const ${name} = \``);
	expect(at, `Orb.svelte no longer declares ${name}`).toBeGreaterThan(-1);
	const start = src.indexOf('`', at) + 1;
	const end = src.indexOf('`', start);
	return src.slice(start, end);
}

/**
 * Every uniform the draw loop sets. A shader that dropped one draws garbage.
 *
 * `uPhases` is a vec3 and not the single `uPhase` it replaced: the three blobs
 * orbit at 1 : 0.73 : 1.31, and one shared phase wrapped at TAU makes the two
 * fractional ones jump every wrap. They integrate separately now.
 */
const UNIFORMS = [
	'uRes',
	'uTime',
	'uLevel',
	'uState',
	'uPhases',
	'uSpin',
	'uCoilSpin',
	'uBreath',
	'uDrift'
];

/** The phases the draw loop wraps at TAU. See the seam test at the bottom. */
const WRAPPED = ['uPhases', 'uSpin', 'uCoilSpin', 'uBreath', 'uDrift'];

test.describe('the orb shader', () => {
	test('has no backtick in its source, which would truncate it', () => {
		const src = readFileSync(SOURCE, 'utf8');
		const body = src.slice(src.indexOf('const FRAG = `') + 14);
		const glsl = body.slice(0, body.indexOf('\n`;\n'));
		// This is the exact bug: a backtick inside the literal ends it early, the
		// rest of the shader becomes JavaScript, and the fragment source stops
		// mid-line. Worth its own assertion because the compile failure it causes
		// points at a line number rather than at the cause.
		expect(glsl.includes('`'), 'a backtick inside the GLSL ends the template literal').toBe(
			false
		);
	});

	test('compiles, links and draws in a real WebGL context', async ({ page }) => {
		await page.goto('/');
		const result = await page.evaluate(
			([vs, fs, uniformNames]: [string, string, string[]]) => {
				const canvas = document.createElement('canvas');
				canvas.width = 256;
				canvas.height = 256;
				const gl = canvas.getContext('webgl', {
					alpha: true,
					antialias: true,
					premultipliedAlpha: false
				});
				if (!gl) return { skipped: 'this browser has no WebGL' };

				const build = (type: number, source: string) => {
					const sh = gl.createShader(type)!;
					gl.shaderSource(sh, source);
					gl.compileShader(sh);
					return gl.getShaderParameter(sh, gl.COMPILE_STATUS)
						? { sh }
						: { error: gl.getShaderInfoLog(sh) ?? 'unknown' };
				};

				const v = build(gl.VERTEX_SHADER, vs);
				if ('error' in v) return { error: `vertex shader: ${v.error}` };
				const f = build(gl.FRAGMENT_SHADER, fs);
				if ('error' in f) return { error: `fragment shader: ${f.error}` };

				const program = gl.createProgram()!;
				gl.attachShader(program, v.sh);
				gl.attachShader(program, f.sh);
				gl.linkProgram(program);
				if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
					return { error: `link: ${gl.getProgramInfoLog(program)}` };
				}
				gl.useProgram(program);

				const missing = uniformNames.filter(
					(name) => gl.getUniformLocation(program, name) === null
				);

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

				gl.viewport(0, 0, 256, 256);
				gl.clearColor(0, 0, 0, 0);
				gl.clear(gl.COLOR_BUFFER_BIT);
				gl.uniform2f(gl.getUniformLocation(program, 'uRes'), 256, 256);
				gl.uniform1f(gl.getUniformLocation(program, 'uTime'), 1.0);
				gl.uniform1f(gl.getUniformLocation(program, 'uLevel'), 0.4);
				gl.uniform1f(gl.getUniformLocation(program, 'uState'), 1.0);
				gl.uniform3f(gl.getUniformLocation(program, 'uPhases'), 1.1, 0.8, 1.4);
				gl.uniform1f(gl.getUniformLocation(program, 'uSpin'), 2.2);
				gl.uniform1f(gl.getUniformLocation(program, 'uCoilSpin'), 1.7);
				gl.uniform1f(gl.getUniformLocation(program, 'uBreath'), 0.5);
				gl.uniform1f(gl.getUniformLocation(program, 'uDrift'), 0.9);
				gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

				const centre = new Uint8Array(4);
				gl.readPixels(128, 128, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, centre);
				const corner = new Uint8Array(4);
				gl.readPixels(3, 3, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, corner);
				return { missing, centre: [...centre], corner: [...corner] };
			},
			[shader('VERT'), shader('FRAG'), UNIFORMS] as [string, string, string[]]
		);

		if ('skipped' in result && result.skipped) {
			test.skip(true, result.skipped);
			return;
		}
		expect(result.error ?? null, 'the orb shader did not build').toBeNull();
		expect(result.missing, 'the draw loop sets uniforms the shader does not declare').toEqual(
			[]
		);
		// The middle of the reactor is opaque and lit; the corner is empty. Both
		// halves matter: a shader that compiles and outputs nothing would pass a
		// compile-only check, and one that floods the quad would pass a
		// "something was drawn" check.
		expect(result.centre![3], 'the middle of the orb is transparent').toBeGreaterThan(200);
		expect(
			Math.max(result.centre![0], result.centre![1], result.centre![2]),
			'the middle of the orb is unlit'
		).toBeGreaterThan(60);
		expect(result.corner![3], 'the orb fills its whole canvas rather than a disc').toBeLessThan(
			40
		);
	});

	/**
	 * The orb must not visibly jump once a cycle.
	 *
	 * Every phase the draw loop keeps is integrated as `(phase + dt * rate) % TAU`,
	 * so the shader has to be exactly TAU-periodic in each one. Where it is not,
	 * the wrap is a hard jump — and the size of it depends on the coefficient the
	 * phase is used with, so it is invisible in review and obvious on screen.
	 *
	 * This has already happened twice. `uSpin` reached the coil pattern multiplied
	 * by SPOKE_SPIN_RATIO * SPOKE_COUNT = 3.5, so every wrap slid the ten plates by
	 * half a segment; the same uniform reached the fine dash ring multiplied by
	 * 1.43 * 64 = 91.52 and slid it by half a dash. The user's report was "the
	 * jarvis animation isnt looped and it looks weird".
	 *
	 * Nothing else catches it. The shader compiles, draws, and looks right in any
	 * single frame — the defect exists only BETWEEN two frames, one wrap apart.
	 * So: render at 0 and at TAU, which the wrap says are the same instant, and
	 * demand the same picture.
	 */
	test('every wrapped phase returns to the same picture after a full turn', async ({ page }) => {
		await page.goto('/');
		const result = await page.evaluate(
			([vs, fs, wrapped]: [string, string, string[]]) => {
				const TAU = Math.PI * 2;
				const N = 192;
				const canvas = document.createElement('canvas');
				canvas.width = N;
				canvas.height = N;
				const gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true });
				if (!gl) return { skipped: 'this browser has no WebGL' };

				const build = (type: number, source: string) => {
					const sh = gl.createShader(type)!;
					gl.shaderSource(sh, source);
					gl.compileShader(sh);
					return sh;
				};
				const program = gl.createProgram()!;
				gl.attachShader(program, build(gl.VERTEX_SHADER, vs));
				gl.attachShader(program, build(gl.FRAGMENT_SHADER, fs));
				gl.linkProgram(program);
				if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
					return { error: `link: ${gl.getProgramInfoLog(program)}` };
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
				gl.enable(gl.BLEND);
				gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
				gl.viewport(0, 0, N, N);
				const at = (name: string) => gl.getUniformLocation(program, name);

				// Every phase held at a fixed value except `which`, which is swept
				// from 0 to a full turn.
				const frame = (which: string, value: number) => {
					gl.clearColor(0, 0, 0, 0);
					gl.clear(gl.COLOR_BUFFER_BIT);
					gl.uniform2f(at('uRes'), N, N);
					gl.uniform1f(at('uTime'), 4);
					gl.uniform1f(at('uLevel'), 0.4);
					// Thinking, so the turbulence ring is live and gets checked too.
					gl.uniform1f(at('uState'), 2);
					for (const name of wrapped) {
						const v = name === which ? value : 0.8;
						if (name === 'uPhases') gl.uniform3f(at(name), v, v, v);
						else gl.uniform1f(at(name), v);
					}
					gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
					const px = new Uint8Array(N * N * 4);
					gl.readPixels(0, 0, N, N, gl.RGBA, gl.UNSIGNED_BYTE, px);
					return px;
				};

				const worst: Record<string, number> = {};
				for (const name of wrapped) {
					const a = frame(name, 0);
					const b = frame(name, TAU);
					let d = 0;
					for (let i = 0; i < a.length; i++) d = Math.max(d, Math.abs(a[i] - b[i]));
					worst[name] = d;
				}
				return { worst };
			},
			[shader('VERT'), shader('FRAG'), WRAPPED] as [string, string, string[]]
		);

		if ('skipped' in result && result.skipped) {
			test.skip(true, result.skipped);
			return;
		}
		expect(result.error ?? null, 'the orb shader did not build').toBeNull();
		// Two or three levels of 255 is float rounding between two draws. The
		// failure this guards against moved 22,265 pixels by up to 151.
		for (const [name, delta] of Object.entries(result.worst!)) {
			expect(delta, `${name} does not come back to itself after a full turn`).toBeLessThanOrEqual(
				3
			);
		}
	});
});
