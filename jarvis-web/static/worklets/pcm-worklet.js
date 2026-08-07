// AudioWorklet processor: capture mic Float32 blocks at the context rate,
// downsample to 16 kHz (linear interpolation), quantize to Int16, batch
// ~1024 samples per message, and report RMS level for the orb.
//
// The DSP functions are copies of src/lib/audio/downsample.ts (worklets
// cannot import the app bundle); keep them in sync.

const TARGET_RATE = 16000;
const BATCH_SAMPLES = 1024; // Int16 samples per posted chunk (~64 ms @ 16 kHz)

function downsample(input, fromRate, toRate) {
	if (fromRate === toRate) return Float32Array.from(input);
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

function floatTo16(input) {
	const out = new Int16Array(input.length);
	for (let i = 0; i < input.length; i++) {
		const s = Math.max(-1, Math.min(1, input[i]));
		out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
	}
	return out;
}

function rms(input) {
	if (input.length === 0) return 0;
	let sum = 0;
	for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
	return Math.sqrt(sum / input.length);
}

class PcmWorklet extends AudioWorkletProcessor {
	constructor(options) {
		super();
		const opts = (options && options.processorOptions) || {};
		this.targetRate = opts.targetRate || TARGET_RATE;
		this.batch = opts.batch || BATCH_SAMPLES;
		// Buffer input at context rate; drain in blocks that map to whole batches.
		this.inputChunks = [];
		this.inputLength = 0;
		this.blockIn = Math.ceil((this.batch * sampleRate) / this.targetRate);
		this.levelCounter = 0;
	}

	process(inputs) {
		const channel = inputs[0] && inputs[0][0];
		if (!channel || channel.length === 0) return true;

		this.inputChunks.push(Float32Array.from(channel));
		this.inputLength += channel.length;

		// RMS roughly every 4 blocks (~10ms @128 frames -> ~40ms updates)
		if (++this.levelCounter >= 4) {
			this.levelCounter = 0;
			this.port.postMessage({ type: 'level', rms: rms(channel) });
		}

		while (this.inputLength >= this.blockIn) {
			const block = new Float32Array(this.blockIn);
			let offset = 0;
			while (offset < this.blockIn) {
				const head = this.inputChunks[0];
				const take = Math.min(head.length, this.blockIn - offset);
				block.set(head.subarray(0, take), offset);
				offset += take;
				if (take === head.length) this.inputChunks.shift();
				else this.inputChunks[0] = head.subarray(take);
			}
			this.inputLength -= this.blockIn;

			const pcm = floatTo16(downsample(block, sampleRate, this.targetRate));
			this.port.postMessage({ type: 'chunk', buffer: pcm.buffer }, [pcm.buffer]);
		}
		return true;
	}
}

registerProcessor('pcm-worklet', PcmWorklet);
