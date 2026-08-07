# P6 — Android Auto: what is actually possible

Short version: **a third-party app cannot be the voice assistant on Android
Auto.** This is an OS/platform constraint, not a bug or a missing feature in
our fork. Jarvis in the car is therefore a *phone-side* experience that
happens to play through the car's speakers.

## The hard constraints (honest list)

1. **Only Google's assistant can own AA voice.** The Android Auto voice
   button (steering wheel / "Hey Google") is hardwired to Google's
   assistant. As of **March 2026, Google removed Assistant from Android Auto
   entirely — Gemini is the only voice layer**. There is no API, setting, or
   role that hands the AA voice pipeline to a third-party app.
2. **No "voice assistant" Car App category.** Android for Cars App Library
   templates cover navigation, POI, media/audio, messaging (via
   `MessagingService` patterns), and IoT (`androidx.car.app.category.IOT`).
   There is no assistant category; an app cannot render a conversational
   surface or grab the mic on the head unit.
3. **HA's existing AA integration is tap-to-control only.** The companion
   app (full flavor; the jarvis flavor inherits it if the car-app module is
   in the minimal set, otherwise it can be added) exposes an IoT template
   list — favorites/entities you tap while parked or in the driving-allowed
   subset. No voice.
4. **Head-unit mic is Google's.** While AA is connected, the car mic is
   routed to the AA stack for Gemini. Third-party phone apps do not receive
   car-mic audio.

## The sanctioned fallback (the only viable hands-free path)

Run **"Hey Jarvis" on the phone itself, in parallel with Android Auto**:

- The **phone's mic** does the wake word + speech capture (the phone is
  mounted/charging in the car anyway; microWakeWord keeps working while AA
  is connected).
- Assist TTS output is ordinary media/assistant audio, so it **routes out
  the active Bluetooth/AA audio link to the car speakers**.
- Nothing renders on the head unit — the orb appears on the phone screen
  only. Keep eyes-off usage voice-only.

Limitations to accept: phone-mic quality vs. cabin noise (mount the phone
close), and if Gemini is mid-response the audio focus arbitration is
whoever grabbed focus last. In practice: don't use both assistants at once.

## Car-BT automation trigger (wake gate in the car)

Design for enabling always-on listening exactly while driving, without
burning battery the rest of the day:

- **Signal:** the companion app's Bluetooth connection sensor
  (`sensor.<phone>_bluetooth_connection` with the car's BT MAC/name in its
  attributes), which the app reports to HA.
- **HA automation:**

  ```yaml
  alias: "Jarvis wake gate: car"
  triggers:
    - trigger: state
      entity_id: sensor.pixel_bluetooth_connection
  actions:
    - choose:
        - conditions: "{{ 'CAR_BT_MAC' in state_attr('sensor.pixel_bluetooth_connection','connected_paired_devices') | default([], true) | join(',') }}"
          sequence:
            - action: notify.mobile_app_pixel
              data:
                message: command_update_sensors   # plus app-side toggle, see below
      default:
        - action: notify.mobile_app_pixel
          data:
            message: command_update_sensors
  ```

  The app-side effect is flipping the wake word service on/off. Until the
  companion exposes a direct "set wake word" notification command, the
  jarvis flavor's `WakeWordGate` handles it locally: it already returns
  `shouldListen = true` whenever car BT is connected (any hour), and the
  gate's `carBtConnected` input comes straight from the phone's own
  `BluetoothProfile` callbacks — no server round trip needed. The HA
  automation is then only used for the reverse direction (e.g. announcing
  or logging), not as the source of truth.
- **Disconnect:** BT drop → gate re-evaluates → detection service stops
  within seconds.

## P6 acceptance gate

1. **DHU (Desktop Head Unit) or real car:** connect AA → Home Assistant
   appears in the launcher → IoT template list of favorite entities is
   browsable and tap-to-toggle works. (This is the documented ceiling for
   the head-unit UI.)
2. **Hands-free round trip over BT:** with AA connected and the phone
   mounted, say "Hey Jarvis, what's the temperature in the living room" →
   wake word fires on the phone, pipeline runs, **TTS answer plays through
   the car speakers**. Nothing appears on the head unit (expected).
3. **Gate behavior:** unplug/disconnect BT → wake word service stops
   (verify: mic indicator gone) — reconnect → resumes.

Frame in all user-facing docs: the head unit belongs to Google; Jarvis rides
along on the phone. If Google ever opens an assistant role for AA we revisit,
but nothing in the current Car App Library roadmap suggests it.
