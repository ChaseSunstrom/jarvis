# Phone automation — designed, scaffolded, off

Jarvis can drive **the house**: lights, locks, media, scenes, the code agent,
research. That is shipped, it goes through `AutomationBridge`, and every action
carries a tier.

Jarvis cannot drive **this phone** — reading whatever app is in front through
an accessibility service, reading notifications, tapping and typing on your
behalf. The interfaces for it exist (`automation/phone/PhoneAutomation.kt`),
the two Android services that would feed it exist because the manifest has
always declared them, and the whole feature is behind a compile-time flag that
is `false` in every build this project produces.

## Why it is off

An assistant that can read every screen on a phone is a different product from
one that turns the lights off, and the difference is not effort — it is what
goes wrong:

* **An accessibility service sees everything.** Banking apps, messages, the
  password manager filling a field. Android offers no way to be selective, and
  no way for the user to tell afterwards what was read.
* **A notification listener sees other apps' private content**: message bodies,
  one-time codes, delivery addresses, from apps that never agreed to it.
* **An injected tap is indistinguishable from your finger** to the app
  receiving it. There is no "this was automation" bit, and no undo.

None of that is made safe by careful code here. It is made *decidable* by being
off until somebody turns it on having read this page.

## What is in the tree

| Piece | State |
|---|---|
| `automation/phone/PhoneAutomation.kt` | the interface: `capabilities()`, `readScreen()`, `act()`, and `available`, which is the flag |
| `automation/accessibility/JarvisAccessibilityService.kt` | calls `disableSelf()` on connect and drops every event while the flag is off |
| `automation/notify/JarvisNotificationListener.kt` | reports itself disconnected and ignores every notification while the flag is off |
| `automation/AutomationBridge.kt` | refuses any `ui_*` or `phone_*` action before it can reach a dispatcher |
| `PolicyStore.automationEnabled` | the runtime master switch, and it now defaults **off** |

Four independent refusals, on purpose. The compile-time flag is the one that
matters; the others are what stop a service that a user enabled in Settings
from quietly working anyway.

## Turning it on

```bash
cd android-app && ./gradlew assembleDebug -PphoneAutomation=true
```

Deliberately awkward, and it is not enough on its own — the switch in Settings
is off too, and every phone action would still be Tier 3, which means a consent
prompt per action on the device itself.

Before shipping such a build, the checks in `docs/ANDROID_DEVICE_TESTS.md`
marked PHONE_AUTOMATION are the ones that need doing, and they need a phone:
enabling the accessibility service, confirming it reads a third-party app,
confirming a tap lands where it was aimed, and confirming that turning the
master switch off actually stops all of it.

## What would need designing before it ships

* **A scope**. "Every app" is not one. Per-app consent, remembered, revocable.
* **A record**. The audit log covers house actions; a screen read is not an
  action and does not appear in it.
* **A refusal path for sensitive fields.** Android marks password fields; it
  does not mark a bank balance.

None of those are written. That is the honest reason this is a scaffold rather
than a feature.
