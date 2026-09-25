package com.you.hermeswidget.config

import android.os.Build
import java.net.URLDecoder
import java.net.URLEncoder

/**
 * Manual pairing helpers.
 *
 * The Android app has no QR scanner, so the CLI prints a copy/paste "URL  code"
 * line. A QR code can still encode [link]; this parser turns that deep link back
 * into a server URL and code. Parsing is pure JVM so it is unit-testable.
 */
object PairingLink {
    const val SCHEME = "hermeswidget"
    private const val PREFIX = "$SCHEME://pair?"

    fun parse(raw: String?): Pair<String, String>? {
        if (raw.isNullOrBlank()) return null
        val trimmed = raw.trim()
        if (!trimmed.startsWith(PREFIX)) return null
        val params = trimmed.substring(PREFIX.length)
            .split("&")
            .mapNotNull { part ->
                val index = part.indexOf('=')
                if (index <= 0) null
                else part.substring(0, index) to decode(part.substring(index + 1))
            }
            .toMap()
        val url = params["url"]?.trim()?.trimEnd('/')
        val code = params["code"]?.trim()
        if (url.isNullOrEmpty() || code.isNullOrEmpty()) return null
        if (!url.startsWith("https://")) return null
        if (!code.matches(Regex("[A-Za-z0-9-]{8,32}"))) return null
        return url to code
    }

    fun link(serverUrl: String, code: String): String {
        return "$SCHEME://pair?url=${encode(serverUrl.trimEnd('/'))}&code=${encode(code)}"
    }

    fun deviceLabel(): String {
        val maker = Build.MANUFACTURER.orEmpty().replaceFirstChar { it.uppercase() }.trim()
        val model = Build.MODEL.orEmpty().trim().ifBlank { "Android device" }
        val release = Build.VERSION.RELEASE.orEmpty().trim()
        val head = listOf(maker, model).filter { it.isNotEmpty() }.joinToString(" ")
        return if (release.isEmpty()) head else "$head (Android $release)"
    }

    private fun decode(value: String): String =
        runCatching { URLDecoder.decode(value, "UTF-8") }.getOrDefault(value)

    private fun encode(value: String): String = URLEncoder.encode(value, "UTF-8")
}
