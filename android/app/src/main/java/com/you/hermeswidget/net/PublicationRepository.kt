package com.you.hermeswidget.net

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest

enum class RefreshOutcome {
    UPDATED,
    NOT_MODIFIED,
    EMPTY,
    UNPAIRED,
    REVOKED,
    OFFLINE,
    ERROR,
    ;

    val retry: Boolean
        get() = this == OFFLINE || this == ERROR
}

data class RefreshResult(
    val outcome: RefreshOutcome,
    val publication: Publication? = null,
    val detail: String? = null,
)

internal fun ensureDirectory(dir: File): Boolean = dir.isDirectory || dir.mkdirs()

/** True when [dir] exists as a directory, creating it only when needed.
 *
 * `mkdirs()` alone reports false for a directory that already exists, which made
 * every asset write after the first fail with "cannot create asset directory".
 */
object PublicationRepository {
    suspend fun refresh(context: Context): RefreshResult = withContext(Dispatchers.IO) {
        val appContext = context.applicationContext
        val baseUrl = SecureStore.baseUrl(appContext) ?: Config.getBackendUrl(appContext)
        val token = SecureStore.token(appContext)
        if (baseUrl.isNullOrBlank() || token.isNullOrBlank()) {
            Config.setConnectionState(appContext, ConnectionState.UNPAIRED, System.currentTimeMillis())
            return@withContext RefreshResult(RefreshOutcome.UNPAIRED)
        }

        val widgetId = Config.getWidgetId(appContext)
        var publication = loadCached(appContext)
        var response = HermesApi.fetchPublication(
            baseUrl,
            widgetId,
            token,
            Config.getPublicationEtag(appContext),
        )

        if (response.code == 304 && publication?.content is PublicationContent.Image &&
            !assetIsValid(appContext, publication.content)
        ) {
            response = HermesApi.fetchPublication(baseUrl, widgetId, token)
        }

        when (response.code) {
            304 -> {
                if (publication == null) {
                    response = HermesApi.fetchPublication(baseUrl, widgetId, token)
                    if (response.code != 200 || response.body == null) {
                        return@withContext response.toResult(
                            appContext,
                            publication,
                            "not modified without cached content",
                        )
                    }
                } else {
                    Config.setConnectionState(appContext, ConnectionState.ONLINE, System.currentTimeMillis())
                    return@withContext RefreshResult(RefreshOutcome.NOT_MODIFIED, publication)
                }
            }
            200 -> Unit
            404 -> {
                clear(appContext)
                Config.setConnectionState(appContext, ConnectionState.ONLINE, System.currentTimeMillis())
                return@withContext RefreshResult(RefreshOutcome.EMPTY)
            }
            401, 403 -> {
                Config.setConnectionState(appContext, ConnectionState.REVOKED, System.currentTimeMillis())
                return@withContext RefreshResult(RefreshOutcome.REVOKED, detail = "pairing expired")
            }
            else -> return@withContext response.toResult(appContext, publication, response.error)
        }

        val raw = response.body
            ?: return@withContext response.toResult(appContext, publication, "missing publication")
        publication = try {
            Publication.parse(raw)
        } catch (e: Exception) {
            Config.setConnectionState(appContext, ConnectionState.ERROR, System.currentTimeMillis())
            return@withContext RefreshResult(RefreshOutcome.ERROR, publication, e.message)
        }
        if (publication.widgetId != widgetId) {
            Config.setConnectionState(appContext, ConnectionState.ERROR, System.currentTimeMillis())
            return@withContext RefreshResult(RefreshOutcome.ERROR, publication, "publication widget id mismatch")
        }

        var assetEtag: String? = null
        val content = publication.content
        if (content is PublicationContent.Image && !publication.isExpired()) {
            val asset = content
            var assetResponse: HttpResult
            if (assetIsValid(appContext, asset)) {
                assetEtag = Config.getAssetEtag(appContext, asset.assetId)
                assetResponse = HttpResult(304, etag = assetEtag)
            } else {
                assetResponse = HermesApi.fetchAsset(
                    baseUrl,
                    asset.assetId,
                    token,
                    Config.getAssetEtag(appContext, asset.assetId),
                )
            }
            if (assetResponse.code == 304 && !assetIsValid(appContext, asset)) {
                assetResponse = HermesApi.fetchAsset(baseUrl, asset.assetId, token)
            }
            when (assetResponse.code) {
                200 -> {
                    val bytes = assetResponse.bytes
                        ?: return@withContext assetFailure(appContext, "asset response was empty")
                    if (bytes.size.toLong() != asset.bytes || sha256(bytes) != asset.sha256) {
                        return@withContext assetFailure(appContext, "asset integrity check failed")
                    }
                    writeAsset(appContext, asset.assetId, bytes)
                    assetEtag = assetResponse.etag
                }
                304 -> {
                    if (!assetIsValid(appContext, asset)) {
                        return@withContext assetFailure(appContext, "asset was not cached")
                    }
                    assetEtag = assetResponse.etag ?: Config.getAssetEtag(appContext, asset.assetId)
                }
                else -> return@withContext assetResponse.toResult(
                    appContext,
                    publication,
                    assetResponse.error,
                )
            }
        }

        if (!Config.setCachedPublication(
                appContext,
                raw,
                response.etag,
                (publication.content as? PublicationContent.Image)?.assetId,
                assetEtag,
            )
        ) {
            return@withContext assetFailure(appContext, "could not persist publication")
        }
        cleanupAssets(appContext, (publication.content as? PublicationContent.Image)?.assetId)
        Config.setConnectionState(appContext, ConnectionState.ONLINE, System.currentTimeMillis())
        RefreshResult(RefreshOutcome.UPDATED, publication)
    }

    fun loadCached(context: Context): Publication? {
        val raw = Config.getCachedPublication(context) ?: return null
        return runCatching { Publication.parse(raw) }.getOrNull()
    }

    suspend fun acknowledgeRenderSubmitted(
        context: Context,
        width: Int,
        height: Int,
    ): Boolean = withContext(Dispatchers.IO) {
        val appContext = context.applicationContext
        val baseUrl = SecureStore.baseUrl(appContext) ?: Config.getBackendUrl(appContext)
        val token = SecureStore.token(appContext)
        val publication = loadCached(appContext)
        if (baseUrl.isNullOrBlank() || token.isNullOrBlank() || publication == null ||
            publication.isExpired() || width <= 0 || height <= 0
        ) {
            return@withContext false
        }
        HermesApi.acknowledgeRender(
            baseUrl,
            publication.widgetId,
            token,
            publication.revision,
            width,
            height,
        ).code in 200..299
    }

    private fun assetFailure(context: Context, detail: String): RefreshResult {
        Config.setConnectionState(context, ConnectionState.ERROR, System.currentTimeMillis())
        return RefreshResult(RefreshOutcome.ERROR, detail = detail)
    }

    private fun HttpResult.toResult(
        context: Context,
        publication: Publication?,
        detail: String?,
    ): RefreshResult {
        val outcome = if (code == -1) RefreshOutcome.OFFLINE else RefreshOutcome.ERROR
        Config.setConnectionState(
            context,
            if (outcome == RefreshOutcome.OFFLINE) ConnectionState.OFFLINE else ConnectionState.ERROR,
            System.currentTimeMillis(),
        )
        return RefreshResult(outcome, publication, detail)
    }

    private fun assetIsValid(context: Context, image: PublicationContent.Image): Boolean {
        val file = runCatching { Config.assetFile(context, image.assetId) }.getOrNull()
            ?: return false
        if (!file.isFile || file.length() != image.bytes || file.length() > 5L * 1024 * 1024) {
            return false
        }
        return runCatching { sha256(file.readBytes()) == image.sha256 }.getOrDefault(false)
    }

    private fun writeAsset(context: Context, assetId: String, bytes: ByteArray) {
        val target = Config.assetFile(context, assetId)
        target.parentFile?.let { parent ->
            check(ensureDirectory(parent)) { "cannot create asset directory" }
        }
        if (target.isFile && runCatching { sha256(target.readBytes()) }.getOrNull() ==
            sha256(bytes)
        ) {
            return
        }
        val temporary = File.createTempFile("asset-", ".tmp", target.parentFile)
        try {
            FileOutputStream(temporary).use { it.write(bytes) }
            if (target.exists() && !target.delete()) {
                throw IllegalStateException("cannot replace invalid asset")
            }
            if (!temporary.renameTo(target)) {
                throw IllegalStateException("cannot commit asset")
            }
        } finally {
            if (temporary.exists()) temporary.delete()
        }
    }

    private fun cleanupAssets(context: Context, keepAssetId: String?) {
        val directory = File(context.filesDir, "publication-assets")
        directory.listFiles()?.forEach { file ->
            if (file.isFile && file.name != keepAssetId) file.delete()
        }
    }

    private fun clear(context: Context) {
        Config.clearCachedPublication(context)
        cleanupAssets(context, null)
    }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { byte ->
            "%02x".format(byte.toInt() and 0xff)
        }
}
