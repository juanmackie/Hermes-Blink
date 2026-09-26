// Backend URL validation allows Tailscale IP/host URLs over HTTPS (see docs/TAILSCALE_HTTPS.md)
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Personal release signing is deliberately supplied outside source control. Gradle
// properties keep the existing invocation in docs/APK_RELEASE.md working, while
// environment variables are useful for a local secret manager or CI secret store.
val signingStoreFile = providers.gradleProperty("hermes.signing.store.file")
    .orElse(providers.environmentVariable("HERMES_ANDROID_STORE_FILE"))
val signingStorePassword = providers.gradleProperty("hermes.signing.store.password")
    .orElse(providers.environmentVariable("HERMES_ANDROID_STORE_PASSWORD"))
val signingKeyAlias = providers.gradleProperty("hermes.signing.key.alias")
    .orElse(providers.environmentVariable("HERMES_ANDROID_KEY_ALIAS"))
val signingKeyPassword = providers.gradleProperty("hermes.signing.key.password")
    .orElse(providers.environmentVariable("HERMES_ANDROID_KEY_PASSWORD"))
val signingValues = listOf(signingStoreFile, signingStorePassword, signingKeyAlias, signingKeyPassword)
val hasAnySigningValue = signingValues.any { it.isPresent }
val hasCompleteSigningValues = signingValues.all { it.isPresent }
check(!hasAnySigningValue || hasCompleteSigningValues) {
    "Personal Android signing is incomplete: provide all of hermes.signing.store.file, " +
        "hermes.signing.store.password, hermes.signing.key.alias, and hermes.signing.key.password " +
        "(or the matching HERMES_ANDROID_* environment variables)."
}
gradle.taskGraph.whenReady {
    val releaseRequested = allTasks.any { task ->
        task.name == "assembleRelease" || task.name == "bundleRelease"
    }
    if (releaseRequested && !hasCompleteSigningValues) {
        throw GradleException(
            "Release packaging requires the existing personal Android signing identity; " +
                "refusing to produce an unsigned artifact."
        )
    }
}

android {
    namespace = "com.you.hermeswidget"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.you.hermeswidget"
        minSdk = 26
        targetSdk = 35
        versionCode = 2
        versionName = "0.2.0"
    }
    buildFeatures {
        compose = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    sourceSets {
        getByName("test") {
            // Shared fixtures (single source of truth for layout v2) — see fixtures/
            resources.srcDir(File(rootProject.projectDir, "../fixtures"))
        }
    }
    signingConfigs {
        if (hasCompleteSigningValues) {
            create("personal") {
                storeFile = file(signingStoreFile.get())
                storePassword = signingStorePassword.get()
                keyAlias = signingKeyAlias.get()
                keyPassword = signingKeyPassword.get()
            }
        }
    }
    buildTypes {
        getByName("release") {
            // v2: release builds forbid cleartext; debug keeps http:// for local dev
            isDebuggable = false
            if (hasCompleteSigningValues) {
                signingConfig = signingConfigs.getByName("personal")
            }
        }
        getByName("debug") {
            isDebuggable = true
        }
    }
}

dependencies {
    // Deliberately minimal. Removed as unused in the v2.1.0 bloat audit (they were never
    // imported): retrofit (HTTP is HttpURLConnection), kotlinx-serialization-json (JSON is
    // org.json), datastore-preferences (storage is SharedPreferences/EncryptedSharedPreferences,
    // and datastore still arrives transitively via glance-appwidget), glance-material3.
    implementation("androidx.appcompat:appcompat:1.7.0")  // only as the AppTheme parent; see themes.xml
    implementation("androidx.glance:glance-appwidget:1.1.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("com.caverock:androidsvg-aar:1.4")
    // User-selected distributor (ntfy, NextPush, embedded FCM, ...); no Google
    // service is required by the app itself.
    implementation("org.unifiedpush.android:connector:3.0.9") {
        // The app already ships AndroidX Security's Tink runtime.  The connector's
        // newer plain-Java Tink artifact duplicates its protobuf classes.
        exclude(group = "com.google.crypto.tink", module = "tink")
    }

    // JVM unit tests: real org.json (the android.jar stub throws "not mocked").
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
