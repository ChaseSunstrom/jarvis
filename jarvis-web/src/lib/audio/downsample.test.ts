import { describe, it, expect } from 'vitest';
import { downsample, floatTo16, downsampleTo16, rms } from './downsample';

describe('downsample', () => {
	it('48k -> 16k yields 1/3 the samples', () => {
		const input = new Float32Array(3072);
		const out = downsample(input, 48000, 16000);
		expect(out.length).toBe(1024);
	});

	it('44.1k -> 16k yields floor(n * 16/44.1) samples', () => {
		const input = new Float32Array(4410);
		const out = downsample(input, 44100, 16000);
		expect(out.length).toBe(Math.floor(4410 / (44100 / 16000)));
	});

	it('same-rate passthrough copies the input', () => {
		const input = Float32Array.from([0.1, -0.2, 0.3]);
		const out = downsample(input, 16000, 16000);
		expect(Array.from(out)).toEqual(Array.from(input));
		expect(out).not.toBe(input);
	});

	it('preserves a sine wave within tolerance', () => {
		const from = 48000;
		const to = 16000;
		const freq = 440;
		const input = new Float32Array(from / 10); // 100 ms
		for (let i = 0; i < input.length; i++) {
			input[i] = Math.sin((2 * Math.PI * freq * i) / from);
		}
		const out = downsample(input, from, to);
		let maxErr = 0;
		for (let i = 0; i < out.length; i++) {
			const expected = Math.sin((2 * Math.PI * freq * i) / to);
			maxErr = Math.max(maxErr, Math.abs(out[i] - expected));
		}
		expect(maxErr).toBeLessThan(0.02); // linear interp error at 440 Hz is small
	});

	it('rejects upsampling', () => {
		expect(() => downsample(new Float32Array(10), 16000, 48000)).toThrow();
	});
});

describe('floatTo16 quantization', () => {
	it('maps full-scale values and clips out-of-range input', () => {
		const out = floatTo16(Float32Array.from([0, 1, -1, 1.5, -2, 0.5]));
		expect(out[0]).toBe(0);
		expect(out[1]).toBe(32767);
		expect(out[2]).toBe(-32768);
		expect(out[3]).toBe(32767); // clipped
		expect(out[4]).toBe(-32768); // clipped
		expect(out[5]).toBe(Math.round(0.5 * 32767));
	});
});

describe('downsampleTo16', () => {
	it('combines downsample and quantize', () => {
		const input = new Float32Array(4800).fill(0.5);
		const out = downsampleTo16(input, 48000);
		expect(out.length).toBe(1600);
		expect(out[100]).toBe(Math.round(0.5 * 32767));
		expect(out).toBeInstanceOf(Int16Array);
	});
});

describe('rms', () => {
	it('is 0 for silence and ~0.707 for a full-scale sine', () => {
		expect(rms(new Float32Array(256))).toBe(0);
		const sine = new Float32Array(1600);
		for (let i = 0; i < sine.length; i++) sine[i] = Math.sin((2 * Math.PI * 100 * i) / 16000);
		expect(rms(sine)).toBeGreaterThan(0.69);
		expect(rms(sine)).toBeLessThan(0.72);
		expect(rms(new Float32Array(0))).toBe(0);
	});
});
