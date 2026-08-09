package ai.jarvis.app.audio

/**
 * Where Jarvis listens and speaks when a headset is involved, as pure logic.
 *
 * The motivating hardware is an over-ear earpiece worn all day — a headset that
 * is both the microphone and the speaker, continuously. That single fact drives
 * every decision in this file, because it creates a feedback loop the phone's
 * own speaker/mic pair does not:
 *
 * ```
 *   TTS ──► earpiece speaker ──► (2 cm of air) ──► earpiece mic ──► VAD
 *    ▲                                                              │
 *    └──────────────── "the user is talking, barge in" ◄────────────┘
 * ```
 *
 * Jarvis hears itself, the energy VAD in `JarvisConversation` reads that as
 * speech, and it interrupts its own sentence. On a phone held at arm's length
 * the acoustic path is lossy enough to stay under the VAD threshold; in an
 * earpiece it is the loudest thing in the room.
 *
 * The fix is not a louder threshold — that would make the user shout to be
 * heard. It is to capture through the platform's **communication** path, which
 * hands the request to the hardware/HAL echo canceller: the AEC knows what is
 * being played and subtracts it from what is captured.
 *
 * ## Why not always use VOICE_COMMUNICATION
 *
 * Because it costs transcription accuracy. `VOICE_COMMUNICATION` applies AEC,
 * noise suppression and (on many devices) AGC, all tuned for a phone call —
 * they are aggressive, they clip word onsets, and Whisper does measurably worse
 * on the result. `VOICE_RECOGNITION` is the source that asks the HAL to leave
 * the signal alone, which is what an STT model wants.
 *
 * So the rule is narrow: pay the accuracy cost **only when there is an echo
 * loop to cancel**, which is exactly when capture and playback are the same
 * physical device. See [CaptureProfile.forRoute].
 *
 * No Android imports — every decision here is a pure function of the route, so
 * it is unit-tested on the JVM and mirrored in `tools/audio_route_test.py`. The
 * Android shim that discovers the route lives in [HeadsetMonitor]; the shim that
 * applies it lives in the callers.
 */

/** The class of audio device Jarvis is currently bound to. */
enum class HeadsetKind {
    /** Phone speaker and phone mic. No headset. */
    NONE,

    /** 3.5 mm / USB-C headset with an inline microphone. */
    WIRED_HEADSET,

    /** Wired output only — headphones with no mic. Capture stays on the phone. */
    WIRED_HEADPHONES,

    /** Classic Bluetooth headset over SCO/HFP. The all-day earpiece case. */
    BLUETOOTH_SCO,

    /** Bluetooth output only (A2DP). Music profile, no usable capture path. */
    BLUETOOTH_A2DP,

    /** LE Audio headset. Bidirectional, and the modern hearing-aid profile. */
    BLE_HEADSET,

    /** USB audio class headset with a capture endpoint. */
    USB_HEADSET;

    /** True if this device can capture, not just play. */
    val hasMic: Boolean
        get() = when (this) {
            WIRED_HEADSET, BLUETOOTH_SCO, BLE_HEADSET, USB_HEADSET -> true
            NONE, WIRED_HEADPHONES, BLUETOOTH_A2DP -> false
        }

    /** True if this device plays audio somewhere other than the phone speaker. */
    val isExternalOutput: Boolean
        get() = this != NONE

    /**
     * True if the device sits in or on the ear, so its speaker is acoustically
     * coupled to its own microphone.
     *
     * Only meaningful together with [hasMic]; it is what separates "a headset
     * that will hear itself" from "a headset whose mic is on a cable clip 30 cm
     * below the drivers". We cannot actually measure coupling, so this is
     * deliberately pessimistic: anything worn is assumed coupled.
     */
    val isEarWorn: Boolean
        get() = when (this) {
            BLUETOOTH_SCO, BLE_HEADSET, WIRED_HEADSET, USB_HEADSET -> true
            NONE, WIRED_HEADPHONES, BLUETOOTH_A2DP -> false
        }
}

/**
 * A resolved audio route: what is connected, and whether the user has opted in
 * to Jarvis using it as a headset rather than merely as speakers.
 *
 * @param kind what is physically connected.
 * @param headsetModeEnabled the user's setting. When false, Jarvis treats even
 *   a connected earpiece as plain output — it will play through it, but will
 *   not capture through it and will not offer warm-link. Defaults to false, so
 *   plugging in a headset never silently changes where the microphone is.
 * @param scoAvailable whether the SCO/HFP link can actually be brought up right
 *   now. A headset can be paired and playing music while its call profile is
 *   busy or unsupported; capturing would then fail or return silence.
 */
data class AudioRoute(
    val kind: HeadsetKind = HeadsetKind.NONE,
    val headsetModeEnabled: Boolean = false,
    val scoAvailable: Boolean = true
) {
    /**
     * True when Jarvis should capture through the headset rather than the phone.
     *
     * Requires all three of: a device that can capture, the user's opt-in, and —
     * for the Bluetooth profiles that need a link brought up — that link being
     * available. Anything less falls back to the phone mic, which always works.
     */
    val capturesThroughHeadset: Boolean
        get() {
            if (!kind.hasMic || !headsetModeEnabled) return false
            return if (kind.needsScoLink) scoAvailable else true
        }

    /**
     * True when playback and capture are the same physical device, and therefore
     * when there is an echo loop for the AEC to cancel.
     */
    val hasEchoLoop: Boolean
        get() = capturesThroughHeadset && kind.isEarWorn

    /**
     * True when a spoken reply can be followed by another question without the
     * user reaching for the phone or saying the wake word again.
     *
     * Gated on [hasEchoLoop] rather than merely on a headset being present: with
     * no echo cancellation, leaving the mic open after a reply means the tail of
     * Jarvis's own sentence re-triggers the VAD and starts a turn against itself.
     * Warm-link without AEC is a loop, not a feature.
     */
    val warmLinkEligible: Boolean
        get() = hasEchoLoop
}

private val HeadsetKind.needsScoLink: Boolean
    get() = this == HeadsetKind.BLUETOOTH_SCO

/**
 * How to open the microphone for a given route.
 *
 * @param useVoiceCommunication true to request `MediaRecorder.AudioSource
 *   .VOICE_COMMUNICATION` (AEC on, accuracy down), false for
 *   `VOICE_RECOGNITION` (raw, accuracy up).
 * @param requestCommunicationDevice true to pin capture and playback to the
 *   headset via `AudioManager.setCommunicationDevice`. Without this the AEC has
 *   no defined reference signal and the platform may route playback to the
 *   phone speaker mid-conversation.
 * @param reason a short human-readable justification, surfaced in the audio
 *   diagnostics screen so a user debugging choppy capture can see which branch
 *   they landed in rather than guessing.
 */
data class CaptureProfile(
    val useVoiceCommunication: Boolean,
    val requestCommunicationDevice: Boolean,
    val reason: String
) {
    companion object {
        /**
         * The whole policy, in one place.
         *
         * Note the third branch: a headset with a mic that is *not* ear-worn
         * (inline mic on a cable, desk USB headset held away) captures through
         * the headset but keeps the accuracy-preserving source, because there is
         * no loop to cancel. Getting that case wrong in the safe direction costs
         * transcription quality on the most common wired hardware, which is why
         * it is a branch rather than a simplification.
         */
        fun forRoute(route: AudioRoute): CaptureProfile = when {
            !route.capturesThroughHeadset -> CaptureProfile(
                useVoiceCommunication = false,
                requestCommunicationDevice = false,
                reason = "phone microphone; raw source for transcription accuracy"
            )

            route.hasEchoLoop -> CaptureProfile(
                useVoiceCommunication = true,
                requestCommunicationDevice = true,
                reason = "ear-worn headset; echo cancellation required so the " +
                    "reply is not heard as a new question"
            )

            else -> CaptureProfile(
                useVoiceCommunication = false,
                requestCommunicationDevice = true,
                reason = "headset microphone, not ear-worn; no echo loop to " +
                    "cancel, so the raw source is kept"
            )
        }
    }
}
