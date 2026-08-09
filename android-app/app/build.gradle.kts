// `java.util.zip.ZipFile(...)` does NOT work in a Gradle Kotlin DSL script: the
// Android plugin contributes a `java` extension accessor, so a leading `java.`
// resolves to that extension rather than to the package root and fails with
// "Unresolved reference: util". The knock-on is worse than the error itself —
// the ZipFile call becomes error-typed, so `zip`, `entry` and every value
// derived from them do too, and the compiler then reports confusing cascade
// failures pages away (an "overload resolution ambiguity" on Int.compareTo in
// assertNoTestHooksInRelease, whose real cause is this line).
//
// Importing the type and calling it unqualified sidesteps the accessor.
import java.util.zip.ZipFile

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

/*
 * Instrumentation-test dependency versions.
 *
 * These belong in `gradle/libs.versions.toml` alongside every other version in
 * this build, and moving them there is a mechanical change: add a `[versions]`
 * entry and a `[libraries]` line, then swap the string below for the `libs.`
 * accessor. They are here rather than there only because the version catalog is
 * owned by another workstream and a concurrent edit to a TOML file is the one
 * kind of merge conflict that breaks every Gradle task at once.
 *
 * Pinned, not `+`: a test suite whose dependencies float is a test suite that
 * can start failing on a day nobody changed anything.
 */
val androidxTestCore = "1.6.1"
val androidxTestRunner = "1.6.2"
val androidxTestRules = "1.6.1"
val androidxTestExtJunit = "1.2.1"
val espresso = "3.6.1"
val uiAutomator = "2.3.0"

android {
    namespace = "ai.jarvis.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "ai.jarvis.app"
        minSdk = 29
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"

        // Instrumented tests (src/androidTest) run the real APK on a real
        // emulator. The JVM unit tests in src/test still cover the pure-logic
        // classes (config/WakeWordGate, config/ServerUrl, ui/ConsentGate, …);
        // these are the ones that need a device to mean anything.
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        debug {
            // Same applicationId as release on purpose: the assistant-role adb
            // commands in the README name ai.jarvis.app/... verbatim.
            isMinifyEnabled = false
        }
        release {
            // Kept off for the first build: R8 + a manifest full of reflectively
            // named services is a bad place to debug your first APK.
            isMinifyEnabled = false
            isShrinkResources = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        // UI is built programmatically (see ui/ and the ported activities).
        viewBinding = false
        dataBinding = false
        compose = false
        // BuildConfig.VERSION_NAME is used in the WebView user agent.
        buildConfig = true
    }

    // Additive: keeps src/{main,test}/kotlin compiled regardless of plugin
    // defaults. The unit tests are plain JVM tests over the pure-logic classes
    // (policy, tiering, parsing, URL/origin handling) — no device required.
    sourceSets.getByName("main").java.srcDir("src/main/kotlin")
    sourceSets.getByName("test").java.srcDir("src/test/kotlin")

    // The instrumented suite and the debug-only hooks it drives. `debug` is a
    // VARIANT source set: nothing under src/debug/ is compiled into, or
    // packaged with, the release APK. That is the whole security argument for
    // ai.jarvis.app.testing.TestHooks existing at all — see the header of that
    // file, and `assertNoTestHooksInRelease` below.
    sourceSets.getByName("debug").java.srcDir("src/debug/kotlin")
    sourceSets.getByName("androidTest").java.srcDir("src/androidTest/kotlin")

    // androidTest compiles against, and instruments, the debug variant. Stated
    // explicitly because the whole instrumented suite depends on it: the tests
    // reference src/debug classes, which only exist in this variant.
    testBuildType = "debug"

    testOptions {
        // Window/transition animations off for the whole instrumented run:
        // Espresso's idling contract does not cover them and they are the
        // classic source of "passes locally, flakes in CI".
        //
        // Note what this does NOT mean. It sets the system animation scales to
        // 0, which JarvisBootAnimation reads (BootTimeline.shouldSkip) and
        // correctly honours by collapsing to its end state. So BootAnimationTest
        // cannot rely on the ambient setting: it restores the scales itself for
        // the duration of that one class and drives the sequence directly.
        animationsDisabled = true

        unitTests {
            // Stubbed android.jar methods return 0/null instead of throwing, so
            // a test that brushes against an Android type fails on its own
            // assertion rather than on "not mocked".
            isReturnDefaultValues = true
        }
    }

    lint {
        // AndroidManifest.xml declares the automation module's components
        // (ai.jarvis.app.automation.**), which are owned by another module and
        // may not exist yet. Lint's MissingClass must not fail the build.
        abortOnError = false
        checkReleaseBuilds = false
    }

    packaging {
        resources.excludes += setOf(
            "/META-INF/AL2.0",
            "/META-INF/LGPL2.1",
            "/META-INF/DEPENDENCIES",
            // Duplicated by the androidx.test / okhttp test artifacts. Excluded
            // rather than picked-first so a genuine collision still fails loudly.
            "/META-INF/LICENSE.md",
            "/META-INF/LICENSE-notice.md",
            "/META-INF/NOTICE.md",
            "/META-INF/INDEX.LIST"
        )
    }

    /**
     * AGP otherwise embeds a Google-signed, encrypted blob listing this app's
     * dependencies in every APK. Nothing in Jarvis reads it, nothing on a
     * degoogled phone can decrypt it, and an opaque encrypted section is a poor
     * fit for an app whose whole pitch is that it is yours and inspectable. It
     * also makes builds non-reproducible.
     */
    dependenciesInfo {
        includeInApk = false
        includeInBundle = false
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.activity.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.service)
    implementation(libs.androidx.work.runtime.ktx)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.coroutines.core)
    implementation(libs.kotlinx.coroutines.android)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)

    // --- instrumented tests (src/androidTest, real device/emulator) ----------
    androidTestImplementation(libs.junit)
    androidTestImplementation("androidx.test:core:$androidxTestCore")
    androidTestImplementation("androidx.test:core-ktx:$androidxTestCore")
    androidTestImplementation("androidx.test:runner:$androidxTestRunner")
    androidTestImplementation("androidx.test:rules:$androidxTestRules")
    androidTestImplementation("androidx.test.ext:junit:$androidxTestExtJunit")
    androidTestImplementation("androidx.test.espresso:espresso-core:$espresso")
    androidTestImplementation("androidx.test.uiautomator:uiautomator:$uiAutomator")

    // The in-process fake jarvis-core the channel tests talk to. Same okhttp
    // version the app itself uses, so the client and the server under test
    // cannot drift apart on WebSocket framing.
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:${libs.versions.okhttp.get()}")
}

/**
 * Fail the build if anything under `ai.jarvis.app.testing` reaches a release
 * artefact.
 *
 * The debug source set already guarantees this — AGP does not compile
 * src/debug/ into release — but "guaranteed by a build-system default" is the
 * kind of guarantee that quietly stops holding when someone adds a source set,
 * a flavour, or a `matchingFallbacks`. The hooks inject server credentials and
 * a synthetic microphone, so the cost of being wrong is high and the cost of
 * checking is one string search over the archive.
 *
 * ## What is searched, and why it is not only the DEX
 *
 * Three encodings, over EVERY entry of the archive rather than only `*.dex`:
 *
 *  * `ai/jarvis/app/testing/` — the DEX type-descriptor form, stored as plain
 *    UTF-8 in the DEX string table.
 *  * `ai.jarvis.app.testing` — the source form, as it appears in a manifest, a
 *    reflective `Class.forName`, or a stack-trace string.
 *  * the same string in UTF-16LE — how a **binary** `AndroidManifest.xml`
 *    stores it. `src/debug/AndroidManifest.xml` declares `TestHostActivity`,
 *    and a manifest entry moved to `src/main` would put a debug-only component
 *    into the release manifest without a single byte changing in any DEX. A
 *    DEX-only scan would have called that clean.
 *
 * Searching every entry costs a few million byte comparisons on a 5 MB APK and
 * removes a whole class of "we only looked in the obvious place" mistake.
 *
 * Runs after `assembleRelease` and `bundleRelease`; costs nothing on a debug
 * build.
 */
val assertNoTestHooksInRelease = tasks.register("assertNoTestHooksInRelease") {
    description = "Verifies ai.jarvis.app.testing.* is absent from every release artefact."
    group = "verification"
    val apkDir = layout.buildDirectory.dir("outputs/apk/release")
    val bundleDir = layout.buildDirectory.dir("outputs/bundle/release")
    outputs.upToDateWhen { false }
    doLast {
        val archives = listOf(apkDir, bundleDir)
            .flatMap { it.get().asFile.listFiles().orEmpty().asIterable() }
            .filter { it.name.endsWith(".apk") || it.name.endsWith(".aab") }
        if (archives.isEmpty()) {
            logger.lifecycle("assertNoTestHooksInRelease: no release artefact to inspect; skipping.")
            return@doLast
        }

        val needles: List<Pair<String, ByteArray>> = listOf(
            "DEX descriptor" to "ai/jarvis/app/testing/".toByteArray(Charsets.UTF_8),
            "UTF-8 class name" to "ai.jarvis.app.testing".toByteArray(Charsets.UTF_8),
            "UTF-16 (binary manifest)" to
                "ai.jarvis.app.testing".toByteArray(Charsets.UTF_16LE),
        )

        fun contains(haystack: ByteArray, needle: ByteArray): Boolean {
            if (needle.isEmpty() || haystack.size < needle.size) return false
            val first = needle[0]
            val last = haystack.size - needle.size
            var i = 0
            while (i <= last) {
                if (haystack[i] == first) {
                    var j = 1
                    while (j < needle.size && haystack[i + j] == needle[j]) j++
                    if (j == needle.size) return true
                }
                i++
            }
            return false
        }

        var entriesScanned = 0
        for (archive in archives) {
            ZipFile(archive).use { zip ->
                for (entry in zip.entries()) {
                    if (entry.isDirectory) continue
                    val bytes = zip.getInputStream(entry).readBytes()
                    entriesScanned++
                    for ((what, needle) in needles) {
                        if (contains(bytes, needle)) {
                            throw GradleException(
                                "SECURITY: ${archive.name}/${entry.name} contains the " +
                                    "$what form of ai.jarvis.app.testing — the " +
                                    "debug-only test hooks leaked into a release build. " +
                                    "See the header of " +
                                    "app/src/debug/kotlin/ai/jarvis/app/testing/TestHooks.kt."
                            )
                        }
                    }
                }
            }
        }
        logger.lifecycle(
            "assertNoTestHooksInRelease: ${archives.size} release artefact(s), " +
                "$entriesScanned entries, clean."
        )
    }
}

tasks.matching { it.name == "assembleRelease" || it.name == "bundleRelease" }.configureEach {
    finalizedBy(assertNoTestHooksInRelease)
}
