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
 * APK.
 *
 * The debug source set already guarantees this — AGP does not compile
 * src/debug/ into release — but "guaranteed by a build-system default" is the
 * kind of guarantee that quietly stops holding when someone adds a source set,
 * a flavour, or a `matchingFallbacks`. The hooks inject server credentials and
 * a synthetic microphone, so the cost of being wrong is high and the cost of
 * checking is one string search over the DEX.
 *
 * Runs as part of `assembleRelease`; costs nothing on a debug build.
 */
val assertNoTestHooksInRelease = tasks.register("assertNoTestHooksInRelease") {
    description = "Verifies ai.jarvis.app.testing.* is absent from the release APK."
    group = "verification"
    val apkDir = layout.buildDirectory.dir("outputs/apk/release")
    outputs.upToDateWhen { false }
    doLast {
        val apks = apkDir.get().asFile.listFiles { f -> f.name.endsWith(".apk") }.orEmpty()
        if (apks.isEmpty()) {
            logger.lifecycle("assertNoTestHooksInRelease: no release APK to inspect; skipping.")
            return@doLast
        }
        // DEX stores type descriptors as plain UTF-8 in its string table, so a
        // byte search over the DEX is enough — no dexlib, no d8 round trip.
        val needle = "ai/jarvis/app/testing/".toByteArray(Charsets.UTF_8)
        for (apk in apks) {
            java.util.zip.ZipFile(apk).use { zip ->
                for (entry in zip.entries()) {
                    if (!entry.name.endsWith(".dex")) continue
                    val dex = zip.getInputStream(entry).readBytes()
                    var found = false
                    var i = 0
                    val last = dex.size - needle.size
                    while (i <= last && !found) {
                        var j = 0
                        while (j < needle.size && dex[i + j] == needle[j]) j++
                        if (j == needle.size) found = true
                        i++
                    }
                    if (found) {
                        throw GradleException(
                            "SECURITY: ${apk.name}/${entry.name} references " +
                                "ai.jarvis.app.testing — the debug-only test hooks " +
                                "leaked into a release build."
                        )
                    }
                }
            }
        }
        logger.lifecycle("assertNoTestHooksInRelease: ${apks.size} release APK(s) clean.")
    }
}

tasks.matching { it.name == "assembleRelease" }.configureEach {
    finalizedBy(assertNoTestHooksInRelease)
}
