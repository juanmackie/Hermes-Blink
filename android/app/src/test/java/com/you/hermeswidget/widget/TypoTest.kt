package com.you.hermeswidget.widget

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Locks the design tokens: what the v2 `style` / `color` / `alignment` fields mean.
 * Before Typo existed the renderer ignored all of them, so `title` and `caption` painted
 * identically. A regression here is exactly that bug coming back.
 */
class TypoTest {

    @Test
    fun `the four style names are four distinct steps`() {
        val specs = listOf("title", "body", "label", "caption").map(Typo::spec)
        assertEquals(4, specs.toSet().size)
    }

    @Test
    fun `title is the hero step and caption is the smallest`() {
        val title = Typo.spec("title")
        val caption = Typo.spec("caption")
        assertTrue("title must be larger than caption", title.sizeSp > caption.sizeSp)
        assertEquals("bold", title.weight)
        assertEquals(Typo.PRIMARY, title.colorHex)
        // Captions are secondary ink, not primary.
        assertEquals(Typo.SECONDARY, caption.colorHex)
    }

    @Test
    fun `unknown or absent style falls back to body`() {
        assertEquals(Typo.spec("body"), Typo.spec(null))
        assertEquals(Typo.spec("body"), Typo.spec(""))
        assertEquals(Typo.spec("body"), Typo.spec("gigantic"))
    }

    @Test
    fun `textStyle gives each style a different font size`() {
        val title = Typo.textStyle("title")
        val caption = Typo.textStyle("caption")
        assertNotEquals(title.fontSize, caption.fontSize)
        assertNotEquals(title.fontWeight, caption.fontWeight)
    }

    @Test
    fun `a color override wins over the scale color`() {
        assertEquals(HexColor.color("#FF0000"), Typo.resolvedColor("caption", "#FF0000"))
        assertEquals(HexColor.color(Typo.SECONDARY), Typo.resolvedColor("caption"))
        // An override that is not a plain hex is ignored, not fatal.
        assertEquals(HexColor.color(Typo.SECONDARY), Typo.resolvedColor("caption", "red"))
        assertEquals(HexColor.color(Typo.SECONDARY), Typo.resolvedColor("caption", "#FF000080"))
    }

    @Test
    fun `hex accepts 3 and 6 digits and rejects everything else`() {
        assertEquals(HexColor.color("#FFFFFF"), HexColor.color("#fff"))
        assertEquals(HexColor.color("#10B981"), HexColor.color("#10b981"))
        assertNull(HexColor.color("#FF000080"))   // 8-digit alpha: ignored
        assertNull(HexColor.color("red"))
        assertNull(HexColor.color(""))
        assertNull(HexColor.color(null))
        assertNull(HexColor.color("#12345"))
    }

    @Test
    fun `font weight only knows normal medium and bold`() {
        assertEquals(androidx.glance.text.FontWeight.Bold, Typo.fontWeight("bold"))
        assertEquals(androidx.glance.text.FontWeight.Medium, Typo.fontWeight("medium"))
        assertEquals(androidx.glance.text.FontWeight.Normal, Typo.fontWeight("normal"))
        // Glance has no Light/SemiBold; an unknown name must degrade, not throw.
        assertEquals(androidx.glance.text.FontWeight.Normal, Typo.fontWeight("semibold"))
    }

    @Test
    fun `alignment maps both naming styles`() {
        assertEquals(androidx.glance.text.TextAlign.Center, Typo.textAlign("center"))
        assertEquals(androidx.glance.text.TextAlign.End, Typo.textAlign("end"))
        assertEquals(androidx.glance.text.TextAlign.End, Typo.textAlign("trailing"))
        assertEquals(androidx.glance.text.TextAlign.Start, Typo.textAlign("start"))
        assertEquals(androidx.glance.text.TextAlign.Start, Typo.textAlign(null))
    }
}

/** The datetime rule: a layout binds an ISO datetime, the device shows a clock time. */
class TimeFormatTest {

    @Test
    fun `iso datetimes become clock times`() {
        assertEquals("09:30", TimeFormat.shortTime("2026-09-16T09:30:00Z"))
        assertEquals("14:00", TimeFormat.shortTime("2026-09-16T14:00:00+02:00"))
        assertEquals("09:30", TimeFormat.shortTime("2026-09-16T09:30:00"))
    }

    @Test
    fun `a date with no time is left alone rather than invented`() {
        assertEquals("2026-09-16", TimeFormat.shortTime("2026-09-16"))
    }

    @Test
    fun `junk and absence both yield null`() {
        assertNull(TimeFormat.shortTime(null))
        assertNull(TimeFormat.shortTime(""))
        assertNull(TimeFormat.epochMillis("not a date"))
        assertNull(TimeFormat.epochMillis(null))
    }

    @Test
    fun `epochMillis reads an offset datetime`() {
        val expected = java.time.Instant.parse("2026-09-16T07:30:00Z").toEpochMilli()
        assertEquals(expected, TimeFormat.epochMillis("2026-09-16T07:30:00Z"))
    }
}

class LayoutStalenessTest {

    private fun layout(ttl: Int?, updatedAt: String?) = WidgetLayout(
        ttlSeconds = ttl,
        updatedAt = updatedAt,
        root = Node(type = "column"),
    )

    @Test
    fun `older than ttl is stale`() {
        val updated = TimeFormat.epochMillis("2026-09-16T07:30:00Z")!!
        val fresh = layout(1800, "2026-09-16T07:30:00Z")
        assertFalse(fresh.isStale(updated + 60_000))
        assertTrue(fresh.isStale(updated + 3_600_000))
    }

    @Test
    fun `no ttl or no timestamp never reports stale`() {
        assertFalse(layout(null, "2026-09-16T07:30:00Z").isStale(Long.MAX_VALUE))
        assertFalse(layout(1800, null).isStale(Long.MAX_VALUE))
        assertFalse(layout(0, "2026-09-16T07:30:00Z").isStale(Long.MAX_VALUE))
    }
}
