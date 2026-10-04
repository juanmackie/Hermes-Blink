package com.you.hermeswidget.net

import org.junit.Assert.*
import org.junit.Test

class AssetDownloadTest {
    private val bytes = "verified pixels".toByteArray()
    private val asset = PublicationContent.Image("asset_${"a".repeat(24)}", "image/png", 4, 4, bytes.size.toLong(), digest(bytes))

    @Test
    fun unavailableVariantCanBeRetriedWithoutPersistingInvalidData() {
        var persisted: ByteArray? = null
        val offline = downloadAsset(asset, { false }, { HttpResult(-1, error = "offline") }, { persisted = it })
        assertTrue(offline.offline)
        assertNull(persisted)
        val bad = downloadAsset(asset, { false }, { HttpResult(200, bytes = "wrong bytes".toByteArray()) }, { persisted = it })
        assertEquals("asset integrity check failed", bad.error)
        assertNull(persisted)
        val recovered = downloadAsset(asset, { false }, { HttpResult(200, bytes = bytes) }, { persisted = it })
        assertNull(recovered.error)
        assertArrayEquals(bytes, persisted)
    }

    @Test
    fun cacheLossAfterConditionalHitRetriesWithoutEtag() {
        val calls = mutableListOf<Boolean>()
        var persisted: ByteArray? = null
        val result = downloadAsset(asset, { false }, { conditional ->
            calls.add(conditional)
            if (conditional) HttpResult(304) else HttpResult(200, bytes = bytes)
        }, { persisted = it })
        assertNull(result.error)
        assertEquals(listOf(true, false), calls)
        assertArrayEquals(bytes, persisted)
    }

    @Test
    fun validCacheAvoidsNetworkAndEmptyOrUnwritableResponsesReportErrors() {
        val cached = downloadAsset(asset, { true }, { error("network must not run") }, { error("write must not run") })
        assertNull(cached.error)
        assertEquals("asset response was empty", downloadAsset(asset, { false }, { HttpResult(200) }, {}).error)
        assertEquals("disk full", downloadAsset(asset, { false }, { HttpResult(200, bytes = bytes) }, { error("disk full") }).error)
    }
}
