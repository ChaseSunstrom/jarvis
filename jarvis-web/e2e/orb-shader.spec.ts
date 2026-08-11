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
const UNIFORMS = ['uRes', 'uTime', 'uLevel', 'uState', 'uPhases', 'uSpin', 'uBreath', 'uDrift'];

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
});
