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
// The commit this APK was built from, resolved once at configuration time. A dirty tree
// is reported as such rather than pretending to be the commit.
val gitDescribe: String = run {
    fun git(vararg args: String): String = try {
        val process = ProcessBuilder(*arrayOf("git") + args)
            .directory(rootProject.projectDir.parentFile)
            .redirectErrorStream(true)
            .start()
        process.inputStream.bufferedReader().use { it.readText() }.trim()
    } catch (e: Exception) {
        ""
    }
    val head = git("rev-parse", "--short=12", "HEAD")
    val dirty = git("status", "--porcelain", "--untracked-files=no")
    when {
        head.isEmpty() -> "unknown"
        dirty.isNotEmpty() -> "$head-dirty"
        else -> head
    }
}
val treeIsDirty: Boolean = run {
    val process = ProcessBuilder("git", "status", "--porcelain", "--untracked-files=no")
        .directory(rootProject.projectDir.parentFile)
        .redirectOutput(ProcessBuilder.Redirect.PIPE)
        .start()
    process.inputStream.bufferedReader().use { it.readText() }.isNotBlank()
}
val requireCleanTree: Boolean =
    providers.gradleProperty("hermes.requireCleanTree").orNull?.toBoolean() == true
gradle.taskGraph.whenReady {
    val releaseRequested = allTasks.any { task ->
        task.name == "assembleRelease" || task.name == "bundleRelease"
    }
    // A build that will be installed must correspond to a commit. A dirty stamp is
    // honest, which is why it exists, and useless for "which build is this phone on?" —
    // every build on the review device carried one. So: release builds refuse, and any
    // build can refuse with -Phermes.requireCleanTree=true. scripts/check-build-provenance.py
    // is the pure-Python half, so the check is verifiable without a JDK.
    if ((releaseRequested || requireCleanTree) && treeIsDirty) {
        val detail = ProcessBuilder("git", "status", "--porcelain", "--untracked-files=no")
            .directory(rootProject.projectDir.parentFile)
            .redirectOutput(ProcessBuilder.Redirect.PIPE)
            .start()
            .let { it.inputStream.bufferedReader().use { r -> r.readText() } }
        throw GradleException(
            "Refusing to build an artifact that cannot be attributed to a commit: the " +
                "working tree has uncommitted changes, so COMMIT_SHA would be stamped " +
                "'$gitDescribe' and the build could never be identified again.\n" +
                "  Commit the change (or stash it) and build again, or pass " +
                "-Phermes.requireCleanTree=false for a local development build.\n" +
                detail.lines().take(10).joinToString("\n") { "    $it" }
        )
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
        // Bumped per release: 10 = 2026-09-27 round 13 — the request action is a Box
        // sibling of the scroll column, and the action counter moved to the callback
        // (round 12 was 9: the action pinned in the header). Round 11: 8 = the action (composition
        // history, rename-tolerant fire detection, one-paste diagnostics). Round 10: 7 = — the composition is
        // laid out for the launcher's reported cell geometry rather than the responsive
        // sample, which is what put the action outside a 270dp 4x2. Round 8 also fixed the
        // attention route, the unparseable workflow and the dead access log.
        // explicit scroll-region height so the pinned action cannot be clipped off the
        // bottom, plus the three-link action trail. CI fails the build when a source
        // change lands with the same versionCode (scripts/check-version-bump.py).
        // 11, not 10: code 10 is spent. The review device holds an APK stamped
        // 0.4.6 / code 10 with COMMIT_SHA 635b0e824c7e-dirty, built from a tree where this
        // bump had been applied but not committed, so that binary is not the committed
        // main. Android installs over it silently and app_build_code cannot tell them
        // apart. See docs/APK_RELEASE.md and scripts/check-build-provenance.py.
        // 12, not 11: 11 is recorded in the release ledger and the review device has been
        // sent one, so reusing it would be the divergence check-build-provenance refuses.
        versionCode = 12
        versionName = "0.4.8"
    }
    buildFeatures {
        compose = true
        // BuildConfig carries the commit this APK was built from. Field round 5: four
        // different APKs shared versionCode 2, so `app_build_code = 2` could not answer
        // "which build is on this phone?" — the very question build reporting was added
        // for. The SHA does answer it, and it is exact.
        buildConfig = true
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

android.defaultConfig.buildConfigField("String", "COMMIT_SHA", "\"$gitDescribe\"")

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
