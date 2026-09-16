// Backend URL validation allows Tailscale IP/host URLs over HTTPS (see backend/TAILSCALE.md)
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.you.hermeswidget"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.you.hermeswidget"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
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
    buildTypes {
        getByName("release") {
            // v2: release builds forbid cleartext; debug keeps http:// for local dev
            isDebuggable = false
        }
        getByName("debug") {
            isDebuggable = true
        }
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.glance:glance-appwidget:1.1.0")
    implementation("androidx.glance:glance-material3:1.1.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("androidx.datastore:datastore-preferences:1.1.1")
    // Backend integration: uses HTTPS URLs; supports Tailscale IP/host URLs

    // JVM unit tests: real org.json (the android.jar stub throws "not mocked").
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
