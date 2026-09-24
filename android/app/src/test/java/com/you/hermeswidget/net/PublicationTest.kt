package com.you.hermeswidget.net

import com.you.hermeswidget.widget.WidgetDimensions
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.Instant

class PublicationTest {
    @Test
    fun parsesTextPublicationAndDoesNotInventVisibility() {
        val publication = Publication.parse(
            envelope(
                content = JSONObject()
                    .put("type", "text")
                    .put("mediaType", "text/plain; charset=utf-8")
                    .put("text", "A useful update"),
            )
        )

        assertEquals(PublicationContent.Text("A useful update"), publication.content)
        assertEquals("A short accessible summary", publication.summary)
        val now = Instant.parse("2026-01-02T00:05:00Z").toEpochMilli()
        assertEquals(PublicationFreshness.FRESH, publication.freshness(now))
    }

    @Test
    fun acceptsSvgAssetAndUsesItsReportedDimensions() {
        val publication = Publication.parse(
            envelope(
                kind = "image",
                content = JSONObject()
                    .put("type", "image")
                    .put("mediaType", "image/svg+xml")
                    .put("assetId", VALID_ASSET_ID)
                    .put("width", 640)
                    .put("height", 480)
                    .put("bytes", 128)
                    .put("sha256", "b".repeat(64)),
            )
        )

        assertEquals(PublicationContent.Image(
            assetId = VALID_ASSET_ID,
            mediaType = "image/svg+xml",
            width = 640,
            height = 480,
            bytes = 128,
            sha256 = "b".repeat(64),
        ), publication.content)
    }

    @Test
    fun assetIdMustMatchTheServerContractNotTheDigest() {
        // Regression: the asset id is "asset_" + 24 hex (store.asset_path), not a
        // 64-char sha256. Rejecting the server's real ids made every image and SVG
        // publication fail to render on the device with "invalid asset id".
        val serverShaped = envelope(
            kind = "image",
            content = imageContent(VALID_ASSET_ID),
        )
        assertEquals(
            VALID_ASSET_ID,
            (Publication.parse(serverShaped).content as PublicationContent.Image).assetId,
        )

        for (bad in listOf("a".repeat(64), "asset_" + "A".repeat(24), "asset_1234", "sha256_" + "a".repeat(24))) {
            assertThrows { Publication.parse(envelope(kind = "image", content = imageContent(bad))) }
        }
    }

    private fun imageContent(assetId: String) = JSONObject()
        .put("type", "image")
        .put("mediaType", "image/png")
        .put("assetId", assetId)
        .put("width", 10)
        .put("height", 10)
        .put("bytes", 10)
        .put("sha256", "b".repeat(64))

    @Test
    fun rejectsMalformedOrUnboundedImageMetadata() {
        val badAsset = imageContent("not-an-asset-id")

        assertThrows {
            Publication.parse(envelope(kind = "image", content = badAsset))
        }
        assertThrows {
            Publication.parse(
                envelope(
                    kind = "image",
                    content = JSONObject(badAsset.toString())
                        .put("width", 16_385),
                )
            )
        }
    }

    @Test
    fun expiryWinsOverFreshness() {
        val now = Instant.parse("2026-01-02T00:00:00Z").toEpochMilli()
        val publication = Publication.parse(
            envelope(
                publishedAt = "2026-01-01T23:00:00Z",
                expiresAt = "2026-01-01T23:30:00Z",
            )
        )

        assertEquals(PublicationFreshness.EXPIRED, publication.freshness(now))
        assertTrue(publication.isExpired(now))
    }

    @Test
    fun ensureDirectorySucceedsWhenTheDirectoryAlreadyExists() {
        // Regression: writeAsset used mkdirs() directly, which returns false for an
        // existing directory, so the second and every later asset write threw
        // "cannot create asset directory" and no image ever rendered again.
        val root = Files.createTempDirectory("hermes-assets").toFile()
        try {
            val nested = File(root, "publication-assets")
            assertTrue(ensureDirectory(nested))
            assertTrue(ensureDirectory(nested))
            assertTrue(nested.isDirectory)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun widgetDimensionsAreConvertedToPixelsAndStayPositive() {
        assertEquals(Pair(360, 220), WidgetDimensions.fromDp(180, 110, 2f))
        assertEquals(Pair(1, 1), WidgetDimensions.fromDp(0, 0, 1f))
    }

    private fun envelope(
        kind: String = "text",
        content: JSONObject = JSONObject()
            .put("type", "text")
            .put("mediaType", "text/plain; charset=utf-8")
            .put("text", "hello"),
        publishedAt: String = "2026-01-02T00:00:00Z",
        expiresAt: String? = null,
    ): String {
        val root = JSONObject()
            .put("version", 1)
            .put("widgetId", "hermes-brief")
            .put("publicationId", "pub_test")
            .put("revision", 1)
            .put("kind", kind)
            .put("title", "A title")
            .put("summary", "A short accessible summary")
            .put("publishedAt", publishedAt)
            .put("content", content)
        if (expiresAt != null) root.put("expiresAt", expiresAt)
        return root.toString()
    }

    private fun assertThrows(block: () -> Unit) {
        try {
            block()
            throw AssertionError("expected validation failure")
        } catch (_: IllegalArgumentException) {
        }
    }

    private companion object {
        const val VALID_ASSET_ID = "asset_c4ec402d1f20c8df4755b3b5"
    }
}
