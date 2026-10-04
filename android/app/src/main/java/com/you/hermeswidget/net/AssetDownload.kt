package com.you.hermeswidget.net

import java.security.MessageDigest

internal data class AssetDownload(val etag: String? = null, val error: String? = null, val offline: Boolean = false)

/** Validate before persistence, and recover a conditional hit whose cache disappeared. */
internal fun downloadAsset(
    asset: PublicationContent.Image,
    cached: () -> Boolean,
    fetch: (conditional: Boolean) -> HttpResult,
    persist: (ByteArray) -> Unit,
): AssetDownload {
    if (cached()) return AssetDownload()
    var response = fetch(true)
    if (response.code == 304 && !cached()) response = fetch(false)
    if (response.code == 304 && cached()) return AssetDownload(response.etag)
    if (response.code != 200) return AssetDownload(error = response.error ?: "asset HTTP ${response.code}", offline = response.code == -1)
    val bytes = response.bytes ?: return AssetDownload(error = "asset response was empty")
    if (bytes.size.toLong() != asset.bytes || digest(bytes) != asset.sha256) {
        return AssetDownload(error = "asset integrity check failed")
    }
    return try {
        persist(bytes)
        AssetDownload(response.etag)
    } catch (error: Exception) {
        AssetDownload(error = error.message ?: "could not persist asset")
    }
}

internal fun digest(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
    .digest(bytes).joinToString("") { "%02x".format(it.toInt() and 0xff) }
