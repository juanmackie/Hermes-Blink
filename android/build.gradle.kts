plugins {
    id("com.android.application") version "9.4.1" apply false
    // No org.jetbrains.kotlin.android: AGP 9 integrates Kotlin support directly and
    // refuses the old plugin. The Compose compiler plugin stays (@Composable sources
    // still need it) pinned to AGP's own embedded KGP (2.2.10): the compose plugin
    // version must match the Kotlin version doing the compiling.
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.20" apply false
}
