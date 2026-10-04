package com.you.hermeswidget.net

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream

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

        if (response.code == 304 && publication != null &&
            publication.images().any { !assetIsValid(appContext, it) }
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
        val missingVariants = mutableListOf<String>()
        for (asset in publication.images().filter { !publication.isExpired() }) {
            val optional = asset.assetId != (publication.content as? PublicationContent.Image)?.assetId
            val download = downloadAsset(
                asset,
                cached = { assetIsValid(appContext, asset) },
                fetch = { conditional ->
                    HermesApi.fetchAsset(baseUrl, asset.assetId, token,
                        if (conditional) Config.getAssetEtag(appContext, asset.assetId) else null)
                },
                persist = { bytes -> writeAsset(appContext, asset.assetId, bytes) },
            )
            if (download.error != null) {
                if (optional) {
                    missingVariants.add(asset.assetId)
                    continue
                }
                if (download.offline) return@withContext HttpResult(-1).toResult(appContext, publication, download.error)
                return@withContext assetFailure(appContext, download.error)
            }
            if (!optional) assetEtag = download.etag ?: Config.getAssetEtag(appContext, asset.assetId)

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
        cleanupAssets(appContext, publication.images().map { it.assetId }.toSet())
        Config.setConnectionState(appContext, ConnectionState.ONLINE, System.currentTimeMillis())
        RefreshResult(RefreshOutcome.UPDATED, publication,
            missingVariants.takeIf { it.isNotEmpty() }?.let { "${it.size} optional visual assets unavailable; primary fallback" })
    }

    fun loadCached(context: Context): Publication? {
        val raw = Config.getCachedPublication(context) ?: return null
        return runCatching { Publication.parse(raw) }.getOrNull()
    }

    suspend fun acknowledgeRenderSubmitted(
        context: Context,
        width: Int,
        height: Int,
        instanceId: String? = null,
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
        val acknowledged = HermesApi.acknowledgeRender(
            baseUrl,
            publication.widgetId,
            token,
            publication.revision,
            width,
            height,
            appContext,
            instanceId,
        ).code in 200..299
        if (acknowledged && Config.markAttentionRendered(appContext, publication.revision)) {
            HermesApi.reportAttention(
                baseUrl, token, publication.widgetId, publication.revision, rendered = 1,
            )
        }
        acknowledged
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

    private fun cleanupAssets(context: Context, keepAssetIds: Set<String>) {
        val directory = File(context.filesDir, "publication-assets")
        directory.listFiles()?.forEach { file ->
            if (file.isFile && file.name !in keepAssetIds) file.delete()
        }
    }

    private fun clear(context: Context) {
        Config.clearCachedPublication(context)
        cleanupAssets(context, emptySet())
    }

    private fun sha256(bytes: ByteArray): String = digest(bytes)
}
