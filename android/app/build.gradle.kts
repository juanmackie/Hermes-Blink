// Backend URL validation allows Tailscale IP/host URLs over HTTPS (see docs/SETUP.md)
plugins {
    id("com.android.application")
    // No org.jetbrains.kotlin.android: AGP 9 integrates Kotlin support directly and
    // refuses the old plugin (issuetracker.google.com/438678642). The Compose compiler
    // plugin stays: @Composable sources still need it.
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
        // Increment for a release tag that ships app changes. Release history and
        // compatibility details live in CHANGELOG.md; CI checks the tag window.
        versionCode = 14
        versionName = "0.5.0"
    }
    buildFeatures {
        compose = true
        // BuildConfig carries the source commit for installed-build diagnostics.
        buildConfig = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    // AGP 9 registers the Kotlin extension itself during its own evaluation (no
    // static KTS accessor for it), so reach it by type once evaluation settles.
    // This is the same JVM_17 floor kotlinOptions used to set.
    afterEvaluate {
        extensions.configure<org.jetbrains.kotlin.gradle.dsl.KotlinAndroidProjectExtension> {
            compilerOptions {
                jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
            }
        }
    }
    sourceSets {
        getByName("test") {
            // AppSurfaceTest reads src/main/res and the activity sources straight off disk
            // (a layout or a theme is not a runtime class, so there is nothing to
            // classload), which means Gradle saw no input change and reported the test
            // task UP-TO-DATE after a token was edited — a gate that does not re-run is a
            // gate that has stopped being one. Declaring the same directory as a test
            // resource makes the dependency real: edit a colour token, and the gates
            // re-run.
            resources.srcDir(File(rootProject.projectDir, "app/src/main/res"))
            resources.srcDir(File(rootProject.projectDir, "app/src/main/java"))
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
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
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
    //
    // Material Components is the one dependency added after the bloat audit, and it is the
    // one the audit itself points at: it is what makes `Theme.Material3` (and with it
    // MaterialButton / TopAppBar / TextInputLayout / MaterialCardView / Snackbar) real
    // instead of hand-rolled. It carries no network, database or analytics surface — only
    // appcompat, recyclerview, coordinatorlayout, constraintlayout, transition and
    // vectordrawable — and it draws every state layer and ripple itself, which is why the
    // per-control <ripple> drawables in res/drawable are now the fallback rather than the
    // only way a control can show a press state.
    implementation("com.google.android.material:material:1.12.0")
    // Kept explicitly even though Material depends on it: AppTheme is still an AppCompat
    // theme (Material3 extends it), and pinning it here documents that.
    implementation("androidx.appcompat:appcompat:1.8.0")  // AppTheme's parent chain; see themes.xml
    implementation("androidx.glance:glance-appwidget:1.1.0")
    implementation("androidx.work:work-runtime-ktx:2.12.0")
    implementation("androidx.security:security-crypto:1.1.0")
    implementation("com.caverock:androidsvg-aar:1.4")
    // User-selected distributor (ntfy, NextPush, embedded FCM, ...); no Google
    // service is required by the app itself.
    implementation("org.unifiedpush.android:connector:3.3.5") {
        // The app already ships AndroidX Security's Tink runtime.  The connector's
        // newer plain-Java Tink artifact duplicates its protobuf classes.
        exclude(group = "com.google.crypto.tink", module = "tink")
    }

    // JVM unit tests: real org.json (the android.jar stub throws "not mocked").
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20260814")
}
