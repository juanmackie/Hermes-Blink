package com.you.hermeswidget.net

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

private const val MAX_PUBLICATION_JSON_BYTES = 512 * 1024
private const val MAX_TEXT_BYTES = 32 * 1024
private const val MAX_SUMMARY_BYTES = 4 * 1024
private const val MAX_TITLE_BYTES = 512
private const val MAX_RASTER_BYTES = 5 * 1024 * 1024
private const val MAX_RASTER_PIXELS = 16_000_000
private const val MAX_RASTER_DIMENSION = 16_384

// Must match the server's immutable asset id contract (store.asset_path:
// "asset_" + secrets.token_hex(12)). Config.assetFile also uses the id as a file
// name, so this is a path-safety check, not just a format rule.
internal val ASSET_ID_PATTERN = Regex("asset_[0-9a-f]{24}")

data class Publication(
    val version: Int,
    val widgetId: String,
    val publicationId: String,
    val revision: Int,
    val kind: String,
    val title: String,
    val summary: String,
    val publishedAt: String,
    val expiresAt: String?,
    val expired: Boolean,
    val priority: String,
    val provenance: String?,
    val darkPalette: Boolean,
    val itemId: String?,
    val actions: List<PublicationAction>,
    val actionStates: Map<String, PublicationActionState>,
    val content: PublicationContent,
    val ticker: PublicationRegion?,
    val question: PublicationQuestion?,
    val variants: Map<String, PublicationVariant>,
) {
    fun isExpired(nowMillis: Long = System.currentTimeMillis()): Boolean {
        if (expired) return true
        val expiry = expiresAt?.let {
            runCatching { Instant.parse(it).toEpochMilli() }.getOrNull()
        } ?: return false
        return expiry <= nowMillis
    }

    fun publishedAtMillis(): Long? = runCatching {
        Instant.parse(publishedAt).toEpochMilli()
    }.getOrNull()

    companion object {
        fun parse(raw: String): Publication {
            require(raw.toByteArray(Charsets.UTF_8).size <= MAX_PUBLICATION_JSON_BYTES) {
                "publication metadata is too large"
            }
            val json = JSONObject(raw)
            val version = json.optInt("version", -1)
            val widgetId = json.requiredString("widgetId")
            val publicationId = json.requiredString("publicationId")
            val revision = json.optInt("revision", -1)
            val kind = json.requiredString("kind")
            val title = json.requiredString("title")
            val summary = json.requiredString("summary")
            val publishedAt = json.requiredString("publishedAt")
            val expiresAt = json.optionalString("expiresAt")
            val expired = json.optBoolean("expired", false)
            val priority = json.optString("priority", "normal")
            require(priority == "normal" || priority == "high") { "invalid publication priority" }
            val provenance = json.optionalString("provenance")
            val darkPalette = json.optBoolean("darkPalette", false)
            require(provenance == null || provenance in setOf("verified", "from_price", "estimate")) {
                "invalid publication provenance"
            }
            val itemId = json.optionalString("itemId")
            val actionsJson = json.optJSONArray("actions") ?: JSONArray()
            val actions = buildList {
                for (index in 0 until actionsJson.length()) {
                    val action = actionsJson.optJSONObject(index) ?: continue
                    val kind = action.optString("kind", "")
                    require(kind in setOf("approve", "snooze", "open")) { "invalid publication action" }
                    val actionItem = action.requiredString("itemId")
                    require(actionItem.length in 1..128) { "invalid publication action itemId" }
                    val actionClass = action.optString("actionClass", "reversible")
                    require(actionClass in setOf(
                        "reversible", "read_only", "dismiss_reminder", "rerun_check",
                        "staged_patch", "flag", "destructive", "external", "irreversible",
                    )) { "invalid publication action class" }
                    add(PublicationAction(
                        kind = kind,
                        itemId = actionItem,
                        label = action.optString("label", kind.replaceFirstChar { it.uppercase() }),
                        actionClass = actionClass,
                        confirmOnDevice = action.optBoolean("confirmOnDevice", false),
                        payload = action.optJSONObject("payload")?.toMap() ?: emptyMap(),
                    ))
                }
            }
            val actionStatesJson = json.optJSONObject("actionStates")
            val actionStates = buildMap {
                if (actionStatesJson != null) {
                    val keys = actionStatesJson.keys()
                    while (keys.hasNext()) {
                        val key = keys.next()
                        val state = actionStatesJson.optJSONObject(key) ?: continue
                        put(key, PublicationActionState(
                            status = state.optString("status", "queued"),
                            result = state.optString("result").takeIf { it.isNotEmpty() },
                            updatedAt = state.optString("updatedAt").takeIf { it.isNotEmpty() },
                        ))
                    }
                }
            }
            val contentJson = json.optJSONObject("content")
                ?: throw IllegalArgumentException("publication content is missing")

            require(version == 1) { "unsupported publication version" }
            require(widgetId.length in 1..128 && widgetId.none { it.isISOControl() || it == '/' }) {
                "invalid widget id"
            }
            require(publicationId.length in 1..128) { "invalid publication id" }
            require(revision >= 1) { "invalid publication revision" }
            require(kind in setOf("text", "image")) { "invalid publication kind" }
            require(title.toByteArray(Charsets.UTF_8).size in 1..MAX_TITLE_BYTES) {
                "invalid publication title"
            }
            require(summary.toByteArray(Charsets.UTF_8).size in 1..MAX_SUMMARY_BYTES) {
                "invalid publication summary"
            }
            if (expiresAt != null) {
                runCatching { Instant.parse(expiresAt) }.getOrThrow()
            }
            runCatching { Instant.parse(publishedAt) }.getOrThrow()

            val content = parseContent(kind, contentJson)

            val regionJson = json.optJSONObject("ticker")
                ?: json.optJSONObject("regions")?.optJSONObject("ticker")
            val ticker = regionJson?.let { parseRegion(it, "ticker") }
            val questionJson = json.optJSONObject("question")
            val variants = buildMap {
                val variantsJson = json.optJSONObject("variants")
                if (variantsJson != null) {
                    val keys = variantsJson.keys()
                    while (keys.hasNext()) {
                        val key = keys.next()
                        val item = variantsJson.optJSONObject(key) ?: continue
                        put(key, PublicationVariant(
                            title = item.optString("title", ""),
                            summary = item.optString("summary", ""),
                            text = item.optString("text", ""),
                        ))
                    }
                }
            }
            val question = questionJson?.let {
                PublicationQuestion(
                    questionId = it.requiredString("questionId"),
                    itemId = it.optionalString("itemId"),
                    prompt = it.requiredString("prompt"),
                    status = it.optString("status", "open"),
                )
            }

            return Publication(
                version = version,
                widgetId = widgetId,
                publicationId = publicationId,
                revision = revision,
                kind = kind,
                title = title,
                summary = summary,
                publishedAt = publishedAt,
                expiresAt = expiresAt,
                expired = expired,
                priority = priority,
                provenance = provenance,
                darkPalette = darkPalette,
                itemId = itemId,
                actions = actions,
                actionStates = actionStates,
                content = content,
                ticker = ticker,
                question = question,
                variants = variants,
            )
        }
    }
}

data class PublicationRegion(
    val title: String,
    val summary: String,
    val content: PublicationContent,
    val priority: String,
    val expiresAt: String?,
    val maxAgeSeconds: Int?,
    val itemId: String?,
    val provenance: String?,
    val pinned: Boolean,
    val rotate: Boolean,
    val rotation: List<PublicationRotationItem>,
    val decayed: Boolean,
)

data class PublicationVariant(
    val title: String,
    val summary: String,
    val text: String,
)

data class PublicationRotationItem(
    val title: String,
    val summary: String,
    val pinned: Boolean,
)

data class PublicationQuestion(
    val questionId: String,
    val itemId: String?,
    val prompt: String,
    val status: String,
)

data class PublicationAction(
    val kind: String,
    val itemId: String,
    val label: String,
    val actionClass: String,
    val confirmOnDevice: Boolean,
    val payload: Map<String, Any>,
)

data class PublicationActionState(
    val status: String,
    val result: String?,
    val updatedAt: String?,
)

sealed class PublicationContent {
    data class Text(val text: String) : PublicationContent()
    data class Image(
        val assetId: String,
        val mediaType: String,
        val width: Int,
        val height: Int,
        val bytes: Long,
        val sha256: String,
    ) : PublicationContent()
}

enum class ConnectionState {
    UNPAIRED,
    ONLINE,
    OFFLINE,
    REVOKED,
    ERROR,
}

enum class PublicationFreshness {
    FRESH,
    AGED,
    STALE,
    EXPIRED,
}

fun Publication.freshness(nowMillis: Long = System.currentTimeMillis()): PublicationFreshness {
    if (isExpired(nowMillis)) return PublicationFreshness.EXPIRED
    val published = publishedAtMillis() ?: return PublicationFreshness.STALE
    val age = nowMillis - published
    return when {
        age < 6 * 60 * 60 * 1000L -> PublicationFreshness.FRESH
        age < 24 * 60 * 60 * 1000L -> PublicationFreshness.AGED
        else -> PublicationFreshness.STALE
    }
}

private fun parseContent(kind: String, contentJson: JSONObject, allowEmptyText: Boolean = false): PublicationContent {
    return when (kind) {
        "text" -> PublicationContent.Text(
            text = contentJson.optString("text", "").also {
                require((allowEmptyText || it.isNotEmpty()) && it.toByteArray(Charsets.UTF_8).size <= MAX_TEXT_BYTES) {
                    "invalid publication text"
                }
            }
        )
        "image" -> PublicationContent.Image(
            assetId = contentJson.requiredString("assetId").also {
                require(ASSET_ID_PATTERN.matches(it)) { "invalid asset id" }
            },
            mediaType = contentJson.requiredString("mediaType").also {
                require(it in setOf("image/png", "image/jpeg", "image/webp", "image/svg+xml")) {
                    "unsupported image type"
                }
            },
            width = contentJson.optInt("width", -1),
            height = contentJson.optInt("height", -1),
            bytes = contentJson.optLong("bytes", -1L),
            sha256 = contentJson.requiredString("sha256").also {
                require(it.matches(Regex("[a-f0-9]{64}"))) { "invalid asset digest" }
            },
        ).also {
            require(it.width in 1..MAX_RASTER_DIMENSION) { "invalid image width" }
            require(it.height in 1..MAX_RASTER_DIMENSION) { "invalid image height" }
            require(it.width.toLong() * it.height <= MAX_RASTER_PIXELS) { "image is too large" }
            require(it.bytes in 1..MAX_RASTER_BYTES) { "invalid image size" }
        }
        else -> throw IllegalArgumentException("invalid publication kind")
    }
}

private fun parseRegion(region: JSONObject, slot: String): PublicationRegion {
    val content = region.optJSONObject("content")
        ?: throw IllegalArgumentException("region $slot content is missing")
    val kind = content.optString("type", "")
    return PublicationRegion(
        title = region.optString("title", ""),
        summary = region.optString("summary", ""),
        content = parseContent(kind, content, allowEmptyText = true),
        priority = region.optString("priority", "normal"),
        expiresAt = region.optionalString("expiresAt"),
        maxAgeSeconds = region.optInt("maxAgeSeconds", -1).takeIf { it >= 0 },
        itemId = region.optionalString("itemId"),
        provenance = region.optionalString("provenance"),
        pinned = region.optBoolean("pinned", false),
        rotate = region.optBoolean("rotate", false),
        rotation = buildList {
            val items = region.optJSONArray("rotation") ?: JSONArray()
            for (index in 0 until items.length()) {
                val item = items.optJSONObject(index) ?: continue
                add(PublicationRotationItem(
                    title = item.optString("title", ""),
                    summary = item.optString("summary", ""),
                    pinned = item.optBoolean("pinned", false),
                ))
            }
        },
        decayed = region.optBoolean("decayed", false),
    )
}

private fun JSONObject.requiredString(name: String): String {
    val value = opt(name) ?: throw IllegalArgumentException("$name is missing")
    require(value is String && value.isNotEmpty()) { "$name is invalid" }
    return value
}

private fun JSONObject.toMap(): Map<String, Any> {
    val result = mutableMapOf<String, Any>()
    val keys = keys()
    while (keys.hasNext()) {
        val key = keys.next()
        if (!isNull(key)) result[key] = get(key)
    }
    return result
}

private fun JSONObject.optionalString(name: String): String? {
    if (!has(name) || isNull(name)) return null
    return requiredString(name)
}
