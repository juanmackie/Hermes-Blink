package com.you.hermeswidget.widget

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Locks the design tokens used by publication rendering.
 * Before Typo existed the renderer ignored the style mapping, so `title` and `caption` painted
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
