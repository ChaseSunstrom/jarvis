// Hands-free triggering.
//
// P1 ships an energy-based VAD ("Hands-free (VAD)" toggle). P4 will plug an
// openWakeWord model in behind the same interface.
//
// To integrate openWakeWord-WASM:
//   1. Bundle the ONNX/tflite model + wasm runtime under static/ (CSP allows
//      only same-origin assets).
//   2. Implement WakeWordDetector.processAudio() to feed 16 kHz Int16 frames
//      to the model and return true on a wake-word hit.
//   3. Swap `EnergyVAD` for the detector where +page.svelte constructs it.

/** Interface an openWakeWord-WASM adapter must implement (P4). */
export interface WakeWordDetector {
	/** Feed 16 kHz mono Int16 PCM; returns true when the wake word fired. */
	processAudio(pcm: Int16Array): boolean;
	reset(): void;
}

export type VadEvent = 'speech-start' | 'speech-end' | null;

export interface VadOptions {
	/** RMS (0..1) above which speech is considered started. */
	startThreshold?: number;
	/** RMS below which speech is considered candidate-ended. */
	endThreshold?: number;
	/** ms of sustained energy required before speech-start fires. */
	minSpeechMs?: number;
	/** ms of silence required before speech-end fires. */
	hangoverMs?: number;
}

/**
 * How long a pause has to last before the turn is considered over.
 *
 * This is the single biggest piece of dead air in a spoken turn, and it is pure
 * waiting: the audio has stopped, the recogniser has not been told, and nothing
 * is happening. It used to be 800 ms, which is *safe* — nobody is ever cut off
 * — and safe here is expensive, because every turn pays it whether or not the
 * speaker was about to continue.
 *
 * 550 ms is past the pauses people actually leave inside a sentence (a comma is
 * 150-250 ms, drawing breath before a subordinate clause is 300-400 ms) and
 * well short of the gap that means "your turn". It takes a quarter of a second
 * off every single turn.
 *
 * Tunable per install rather than hard-coded, because rooms differ: `voice:
 * hangover_ms:` in configuration.yaml reaches this through /api/config. Raise
 * it if you are being cut off; lower it if you are waiting.
 */
export const DEFAULT_HANGOVER_MS = 550;

/**
 * Simple energy VAD driven by RMS levels from the mic worklet
 * (called roughly every 40 ms).
 */
export class EnergyVAD {
	private opts: Required<VadOptions>;
	private speaking = false;
	private aboveSince: number | null = null;
	private belowSince: number | null = null;

	constructor(opts: VadOptions = {}) {
		this.opts = {
			// Ten times lower than the 0.02/0.01 this shipped with, matching the
			// phone. Both move together: they are a hysteresis, and a start edge
			// below the end edge would open a turn and immediately start counting
			// it as silence.
			//
			// The browser applies automatic gain by default, so this surface was
			// the less wrong of the two — but the same room and the same voice
			// should not need to be louder for the console than for the app.
			startThreshold: opts.startThreshold ?? 0.002,
			endThreshold: opts.endThreshold ?? 0.001,
			minSpeechMs: opts.minSpeechMs ?? 120,
			hangoverMs: opts.hangoverMs ?? DEFAULT_HANGOVER_MS
		};
	}

	get isSpeaking(): boolean {
		return this.speaking;
	}

	reset(): void {
		this.speaking = false;
		this.aboveSince = null;
		this.belowSince = null;
	}

	/** Feed an RMS sample; returns a VAD transition event or null. */
	feed(rmsLevel: number, now: number = Date.now()): VadEvent {
		if (!this.speaking) {
			if (rmsLevel >= this.opts.startThreshold) {
				this.aboveSince ??= now;
				if (now - this.aboveSince >= this.opts.minSpeechMs) {
					this.speaking = true;
					this.belowSince = null;
					return 'speech-start';
				}
			} else {
				this.aboveSince = null;
			}
			return null;
		}
		if (rmsLevel < this.opts.endThreshold) {
			this.belowSince ??= now;
			if (now - this.belowSince >= this.opts.hangoverMs) {
				this.speaking = false;
				this.aboveSince = null;
				return 'speech-end';
			}
		} else {
			this.belowSince = null;
		}
		return null;
	}
}
