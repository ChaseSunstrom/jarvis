// Microphone capture: getUserMedia -> AudioWorklet (downsample to 16 kHz
// Int16 batches + RMS level). Browser-only; do not import from Node tests.

export interface CaptureCallbacks {
	/** ~1024-sample Int16 PCM chunks at 16 kHz. */
	onChunk?: (pcm: Int16Array) => void;
	/** RMS level 0..1 of the raw mic signal (for orb + VAD). */
	onLevel?: (rms: number) => void;
}

export class MicCapture {
	private ctx: AudioContext | null = null;
	private stream: MediaStream | null = null;
	private node: AudioWorkletNode | null = null;
	running = false;

	constructor(private cb: CaptureCallbacks = {}) {}

	async start(): Promise<void> {
		if (this.running) return;
		this.stream = await navigator.mediaDevices.getUserMedia({
			audio: {
				channelCount: 1,
				sampleRate: 16000,
				echoCancellation: true,
				noiseSuppression: true,
				autoGainControl: true
			}
		});
		this.ctx = new AudioContext();
		await this.ctx.audioWorklet.addModule('/worklets/pcm-worklet.js');
		const source = this.ctx.createMediaStreamSource(this.stream);
		this.node = new AudioWorkletNode(this.ctx, 'pcm-worklet', {
			numberOfInputs: 1,
			numberOfOutputs: 1,
			processorOptions: { targetRate: 16000, batch: 1024 }
		});
		this.node.port.onmessage = (e: MessageEvent) => {
			const msg = e.data;
			if (msg?.type === 'chunk') this.cb.onChunk?.(new Int16Array(msg.buffer));
			else if (msg?.type === 'level') this.cb.onLevel?.(msg.rms);
		};
		// Keep the node pulled by the graph; its output is silent.
		const sink = this.ctx.createGain();
		sink.gain.value = 0;
		source.connect(this.node);
		this.node.connect(sink);
		sink.connect(this.ctx.destination);
		if (this.ctx.state === 'suspended') await this.ctx.resume();
		this.running = true;
	}

	async stop(): Promise<void> {
		this.running = false;
		this.node?.port.close();
		this.node?.disconnect();
		this.stream?.getTracks().forEach((t) => t.stop());
		if (this.ctx && this.ctx.state !== 'closed') await this.ctx.close();
		this.ctx = null;
		this.stream = null;
		this.node = null;
	}
}
