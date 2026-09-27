package com.you.hermeswidget.net

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import org.json.JSONObject

/**
 * Which build this phone is running.
 *
 * A device that renders the wrong thing is almost always a build problem, and the server
 * cannot answer "which build was this?" if the app never says. This is read from the
 * *installed* package rather than the build constants, so a sideloaded or updated APK
 * reports what is actually on the phone.
 *
 * It is deliberately narrow: an app version, a build code and the OS API level. Nothing
 * that identifies a person, a place or an account. Every value is optional at the far end
 * — an older server ignores unknown headers, and a malformed value is dropped rather
 * than refused, because a version string must never be able to break a publication fetch.
 */
object AppIdentity {

    data class Info(
        val appVersion: String?,
        val appBuildCode: Long?,
        val osSdk: Int,
        /** The commit this APK was built from; the exact answer to "which build is this?". */
        val appBuildSha: String? = null,
    ) {
        /** True when we have something worth telling the server. */
        val isReportable: Boolean get() = !appVersion.isNullOrBlank() || appBuildCode != null

        fun toJson(): JSONObject = JSONObject()
            .put("appVersion", appVersion ?: JSONObject.NULL)
            .put("appBuildCode", appBuildCode ?: JSONObject.NULL)
            .put("osSdk", osSdk)
            .put("appBuildSha", appBuildSha ?: JSONObject.NULL)
    }

    /** Cached: PackageManager reads are cheap but the poll runs often. */
    @Volatile
    private var cached: Info? = null

    @Volatile
    private var appContext: Context? = null

    /**
     * Hand the process an application context once, so the static API layer can name the
     * build on a request that has no context of its own. Idempotent.
     */
    fun attach(context: Context) {
        val application = context.applicationContext ?: context
        if (appContext !== application) {
            appContext = application
            cached = null
        }
    }

    fun of(context: Context?): Info {
        cached?.let { return it }
        val resolved = context?.applicationContext?.let { read(it) } ?: return fallback()
        cached = resolved
        return resolved
    }

    private fun read(appContext: Context): Info {
        return try {
            val info = appContext.packageManager.getPackageInfo(appContext.packageName, 0)
            Info(
                appVersion = info.versionName?.takeIf { it.isNotBlank() },
                appBuildCode = longVersionCode(info),
                osSdk = Build.VERSION.SDK_INT,
                appBuildSha = com.you.hermeswidget.BuildConfig.COMMIT_SHA
                    ?.takeIf { it.isNotBlank() && it != "unknown" },
            )
        } catch (_: PackageManager.NameNotFoundException) {
            fallback()
        } catch (_: RuntimeException) {
            // A broken PackageManager must not take the refresh worker down with it.
            fallback()
        }
    }

    private fun fallback(): Info = Info(
        appVersion = null,
        appBuildCode = null,
        osSdk = Build.VERSION.SDK_INT,
        appBuildSha = null,
    )

    private fun longVersionCode(info: android.content.pm.PackageInfo): Long? = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            info.longVersionCode
        } else {
            @Suppress("DEPRECATION")
            info.versionCode.toLong()
        }
    } catch (_: RuntimeException) {
        null
    }

    /** `0.2.0 (200)` — the one-line form Diagnostics shows and the header carries. */
    fun describe(info: Info): String = when {
        info.appVersion != null && info.appBuildCode != null ->
            "${info.appVersion} (build ${info.appBuildCode})"
        info.appVersion != null -> info.appVersion
        info.appBuildCode != null -> "build ${info.appBuildCode}"
        else -> "unknown build"
    }

    /** The `X-Hermes-App-Version` value, or null when there is nothing to say. */
    fun versionHeader(appVersion: String?): String? =
        appVersion?.trim()?.takeIf { it.isNotEmpty() && it.length <= MAX_VERSION_LENGTH }

    /** The `X-Hermes-App-Build` value, or null when the platform did not report one. */
    fun buildHeader(appBuildCode: Long?): String? =
        appBuildCode?.takeIf { it in 0..MAX_BUILD_CODE }?.toString()

    /** The `X-Hermes-App-Sha` value: short hex from git, or a `-dirty` marker. */
    fun shaHeader(appBuildSha: String?): String? =
        appBuildSha?.trim()?.takeIf { it.isNotEmpty() && it.length <= MAX_SHA_LENGTH }

    const val MAX_VERSION_LENGTH = 32
    const val MAX_BUILD_CODE = 2_147_483_647L
    const val MAX_SHA_LENGTH = 20

    /** Testable pure form: the `client` body block. */
    fun clientBlock(appVersion: String?, appBuildCode: Long?, osSdk: Int): JSONObject =
        Info(appVersion, appBuildCode, osSdk).toJson()
}
