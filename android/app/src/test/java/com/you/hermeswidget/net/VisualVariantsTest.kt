package com.you.hermeswidget.net

import com.you.hermeswidget.widget.Breakpoints
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class VisualVariantsTest {
    private fun image(id: String = "0") = JSONObject()
        .put("type", "image").put("mediaType", "image/png")
        .put("assetId", "asset_${id.repeat(24)}")
        .put("width", 960).put("height", 320).put("bytes", 1024)
        .put("sha256", "a".repeat(64))

    private fun envelope(variants: JSONObject = JSONObject()) = JSONObject()
        .put("version", 1).put("widgetId", "visual").put("publicationId", "pub_visual")
        .put("revision", 1).put("kind", "image").put("title", "Takeaway")
        .put("summary", "Accessible evidence summary").put("publishedAt", "2026-10-03T00:00:00Z")
        .put("content", image()).put("visualVariants", variants)
        .toString()

    @Test
    fun geometryAndPaletteChooseOnlyMatchingVariantAndKeepPrimaryForExpansion() {
        val variants = JSONObject()
        val expected = mutableMapOf<String, String>()
        var index = 1
        for (band in listOf("m", "l")) for (layout in listOf("narrow", "wide")) for (palette in listOf("light", "dark")) {
            val key = "$band-$layout-$palette"
            val descriptor = image(index.toString())
            variants.put(key, descriptor)
            expected[key] = descriptor.getString("assetId")
            index++
        }
        val publication = Publication.parse(envelope(variants))
        assertEquals(9, publication.images().size)
        for (height in listOf(185f, 276f, 300f, 412f, 700f)) for (width in listOf(110f, 210f, 245f, 407f, 900f)) for (dark in listOf(false, true)) {
            val key = "${if (height < 300f) "m" else "l"}-${if (width < 245f) "narrow" else "wide"}-${if (dark) "dark" else "light"}"
            assertEquals(expected[key], publication.visualFor(width, height, dark)?.assetId)
        }
        assertEquals("asset_${"0".repeat(24)}", (publication.content as PublicationContent.Image).assetId)
    }

    @Test
    fun oldPublicationsAndMissingMatchingDescriptorsFallBackToPrimary() {
        val old = JSONObject(envelope()).apply { remove("visualVariants") }
        assertEquals(1, Publication.parse(old.toString()).images().size)
        val publication = Publication.parse(envelope(JSONObject().put("m-wide-dark", image("1"))))
        assertEquals(publication.content, publication.visualFor(210f, 412f, false))
    }

    @Test(expected = IllegalArgumentException::class)
    fun unsafeAssetIdInOptionalVariantIsRejected() {
        Publication.parse(envelope(JSONObject().put("m-wide-dark", image("1").put("assetId", "../secrets"))))
    }

    @Test
    fun visualBudgetReservesChromeHeroTickerAndQuestionAtLargeFontSizes() {
        for (width in listOf(110f, 210f, 245f, 407f, 900f)) for (height in listOf(56f, 160f, 185f, 276f, 300f, 412f, 700f)) for (scale in listOf(1f, 1.5f, 2f)) {
            val spec = Breakpoints.spec(width, height).copy(fontScale = scale)
            val reserved = (spec.heroMaxLines * 22f + if (spec.summaryMaxLines > 0) 17f else 0f) * scale +
                8f + (if (spec.showsTicker) 19f * scale else 0f) + (if (spec.showsQuestion) 19f * scale else 0f)
            assertTrue(spec.imageHeightDp >= 0)
            assertTrue(spec.imageHeightDp.toFloat() <= (spec.scrollHeightDp - reserved).coerceAtLeast(0f) + 0.5f)
            if (!spec.showsBody) assertEquals(0, spec.imageHeightDp)
        }
    }
}
