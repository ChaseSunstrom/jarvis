// Pure DSP helpers shared (by copy) with static/worklets/pcm-worklet.js.
// If you change these, mirror the change in the worklet.

/**
 * Downsample Float32 PCM with linear interpolation. Handles any
 * fromRate >= toRate (e.g. 48000 -> 16000, 44100 -> 16000).
 */
export function downsample(input: Float32Array, fromRate: number, toRate: number): Float32Array {
	if (fromRate === toRate) return Float32Array.from(input);
	if (fromRate < toRate) throw new Error('upsampling not supported');
	const ratio = fromRate / toRate;
	const outLen = Math.floor(input.length / ratio);
	const out = new Float32Array(outLen);
	for (let i = 0; i < outLen; i++) {
		const pos = i * ratio;
		const i0 = Math.floor(pos);
		const i1 = Math.min(i0 + 1, input.length - 1);
		const frac = pos - i0;
		out[i] = input[i0] * (1 - frac) + input[i1] * frac;
	}
	return out;
}

/** Convert Float32 [-1, 1] samples to Int16, clipping out-of-range input. */
export function floatTo16(input: Float32Array): Int16Array {
	const out = new Int16Array(input.length);
	for (let i = 0; i < input.length; i++) {
		const s = Math.max(-1, Math.min(1, input[i]));
		out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
	}
	return out;
}

/** Downsample then quantize in one step (what the worklet does per batch). */
export function downsampleTo16(input: Float32Array, fromRate: number, toRate = 16000): Int16Array {
	return floatTo16(downsample(input, fromRate, toRate));
}

/** Root-mean-square level of a Float32 block, 0..1. */
export function rms(input: Float32Array): number {
	if (input.length === 0) return 0;
	let sum = 0;
	for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
	return Math.sqrt(sum / input.length);
}
