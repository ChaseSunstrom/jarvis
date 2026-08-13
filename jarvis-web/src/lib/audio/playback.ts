// TTS playback through an AnalyserNode (orb levels) with hard stop for
// barge-in. Browser-only.

export class Player {
	private ctx: AudioContext | null = null;
	private analyser: AnalyserNode | null = null;
	private sources = new Set<AudioBufferSourceNode>();
	// `Uint8Array<ArrayBuffer>`, not a bare `Uint8Array`: since TypeScript 5.7
	// the array is generic in its buffer, a bare one widens to ArrayBufferLike,
	// and `getByteTimeDomainData` will not take a view that might be over a
	// SharedArrayBuffer.
	private timeData: Uint8Array<ArrayBuffer> | null = null;

	private ensure(): AudioContext {
		if (!this.ctx) {
			this.ctx = new AudioContext();
			this.analyser = this.ctx.createAnalyser();
			this.analyser.fftSize = 256;
			this.analyser.connect(this.ctx.destination);
			this.timeData = new Uint8Array(this.analyser.fftSize);
		}
		return this.ctx;
	}

	/** Fetch (via the /api/tts proxy), decode and play. Resolves when done. */
	async play(url: string): Promise<void> {
		const ctx = this.ensure();
		if (ctx.state === 'suspended') await ctx.resume();
		const res = await fetch(url);
		if (!res.ok) throw new Error(`tts fetch failed: ${res.status}`);
		const bytes = await res.arrayBuffer();
		const buffer = await ctx.decodeAudioData(bytes);
		return new Promise<void>((resolve) => {
			const source = ctx.createBufferSource();
			source.buffer = buffer;
			source.connect(this.analyser!);
			this.sources.add(source);
			source.onended = () => {
				this.sources.delete(source);
				resolve();
			};
			source.start();
		});
	}

	/** Current output level 0..1 (RMS of the analyser time-domain data). */
	level(): number {
		if (!this.analyser || !this.timeData) return 0;
		this.analyser.getByteTimeDomainData(this.timeData);
		let sum = 0;
		for (let i = 0; i < this.timeData.length; i++) {
			const v = (this.timeData[i] - 128) / 128;
			sum += v * v;
		}
		return Math.sqrt(sum / this.timeData.length);
	}

	get playing(): boolean {
		return this.sources.size > 0;
	}

	/** Barge-in: stop every scheduled source immediately. */
	stopAll(): void {
		for (const s of this.sources) {
			try {
				s.stop();
			} catch {
				// already stopped
			}
		}
		this.sources.clear();
	}
}
