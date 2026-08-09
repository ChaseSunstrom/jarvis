plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "ai.jarvis.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "ai.jarvis.app"
        minSdk = 29
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        // No test runner wired yet; unit tests for the pure-logic classes
        // (config/WakeWordGate, config/ServerUrl) run on the JVM.
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

    testOptions {
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
            "/META-INF/DEPENDENCIES"
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
}
