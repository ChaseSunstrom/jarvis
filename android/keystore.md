# Signing & distribution (own keystore + GitHub Releases + Obtainium)

The Jarvis companion app is a fork, so it is signed with **our own keystore**,
never Play/upstream keys. Signature identity is what makes updates install
cleanly and keeps app data across updates — guard the keystore accordingly.

## 1. Generate the keystore (once)

```bash
keytool -genkeypair -v \
  -keystore jarvis-release.keystore \
  -alias jarvis \
  -keyalg RSA -keysize 4096 \
  -validity 10000 \
  -dname "CN=Jarvis Companion, OU=Jarvis, O=Selfhosted"
```

Store the keystore and its passwords in the password manager and an offline
backup. **If the keystore is lost, every device must uninstall/reinstall**
(new signature = Android refuses the update), which on GrapheneOS also wipes
app data and clears the assistant role.

## 2. Build and sign

```bash
# in the fork checkout, after android/apply-to-fork.sh
./gradlew :app:assembleJarvisRelease

# zipalign is handled by AGP for unsigned release outputs on recent versions;
# if you use the -unsigned artifact, align first:
zipalign -f -p 4 \
  app/build/outputs/apk/jarvis/release/app-jarvis-release-unsigned.apk \
  jarvis-release-aligned.apk

apksigner sign \
  --ks jarvis-release.keystore --ks-key-alias jarvis \
  --out jarvis-companion-vX.Y.Z.apk \
  jarvis-release-aligned.apk

apksigner verify --print-certs jarvis-companion-vX.Y.Z.apk
```

Record the SHA-256 cert digest printed by `apksigner verify --print-certs` —
you will compare it on first install.

Alternatively, configure a `jarvisRelease` signing config in the fork's
Gradle (keystore path/passwords via `~/.gradle/gradle.properties`, never
committed) so `assembleJarvisRelease` emits a signed APK directly.

## 3. Publish to GitHub Releases

Create a release on your fork repo and attach `jarvis-companion-vX.Y.Z.apk`.
Tag names should be sortable (`v2026.8.1-jarvis`); Obtainium tracks the
latest release.

## 4. Obtainium on the phone

1. Install Obtainium (from GitHub Releases or F-Droid/Accrescent).
2. **Add App** → paste your fork's GitHub URL (`https://github.com/<you>/android`).
3. Obtainium detects release APKs; pick the jarvis APK if multiple are attached.
4. On **first install**, verify the signature out-of-band:

   ```bash
   adb shell pm path io.homeassistant.companion.android.jarvis
   adb pull <path> /tmp/installed.apk
   apksigner verify --print-certs /tmp/installed.apk
   # compare SHA-256 digest against the one recorded at signing time
   ```

   After the first install, Android itself enforces same-signature on every
   update — the manual check is only needed once.

## 5. Why signature continuity matters (and what it does NOT cover)

- **Updates (same signature):** app data, permissions, and the app's secure
  storage survive. This is why all releases must use the same keystore.
- **GrapheneOS assistant role:** the `assistant` and
  `voice_interaction_service` Secure Settings are **cleared on every
  reinstall/update anyway** — signature continuity does not save you here.
  After every Obtainium update, re-run:

  ```bash
  ../scripts/adb-jarvis-role.sh
  ```

  (Or re-select the assistant by hand: Settings → Apps → Default apps →
  Digital assistant app.) See `docs/android.md` for details.
