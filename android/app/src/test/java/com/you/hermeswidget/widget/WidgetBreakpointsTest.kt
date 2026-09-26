package com.you.hermeswidget.widget

import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The ladder is the only thing standing between a 4x1 and a 4x4, and it is pure maths, so
 * it is tested over the canonical Android dp ranges rather than eyeballed. A regression
 * here is a clipped action or a wrong variant on a real launcher.
 */
class WidgetBreakpointsTest {


    // --- band edges (E2: 2x1/4x1 56-130, 2x2/4x2 115-276, 4x3 185-422) -------------

    @Test
    fun `band edges follow the canonical height ranges`() {
        assertEquals(WidgetBand.XS, WidgetBand.of(56f))
        assertEquals(WidgetBand.XS, WidgetBand.of(129.9f))
        assertEquals(WidgetBand.S, WidgetBand.of(130f))
        assertEquals(WidgetBand.S, WidgetBand.of(184.9f))
        assertEquals(WidgetBand.M, WidgetBand.of(185f))
        assertEquals(WidgetBand.M, WidgetBand.of(299.9f))
        assertEquals(WidgetBand.L, WidgetBand.of(300f))
        assertEquals(WidgetBand.L, WidgetBand.of(422f))
    }

    @Test
    fun `2x1 and 4x1 land in xs`() {
        assertEquals(WidgetBand.XS, WidgetBand.of(109f, 56f))
        assertEquals(WidgetBand.XS, WidgetBand.of(306f, 129.9f))
        assertEquals(WidgetBand.XS, WidgetBand.of(245f, 56f))
        assertEquals(WidgetBand.XS, WidgetBand.of(624f, 120f))
        // 130dp is the ladder's first S row: E2's 4x1 maximum is tall enough for the
        // one-line summary the S band adds, and the footer still stays out of the way.
        assertEquals(WidgetBand.S, WidgetBand.of(624f, 130f))
    }

    @Test
    fun `2x2 and the 4x2 floor land in s or m by height`() {
        assertEquals(WidgetBand.S, WidgetBand.of(109f, 130f))
        assertEquals(WidgetBand.S, WidgetBand.of(306f, 184f))
        assertEquals(WidgetBand.M, WidgetBand.of(245f, 185f))
        assertEquals(WidgetBand.M, WidgetBand.of(624f, 276f))
    }

    @Test
    fun `4x3 and 4x4 land in l`() {
        assertEquals(WidgetBand.L, WidgetBand.of(245f, 300f))
        assertEquals(WidgetBand.L, WidgetBand.of(624f, 422f))
        assertEquals(WidgetBand.L, WidgetBand.of(407f, 412f))
    }

    // --- variant key ladder (Task 2) --------------------------------------------

    @Test
    fun `4x1 asks for the 2x2 text because it is the smallest budget`() {
        assertEquals("2x2", Breakpoints.spec(624f, 130f).variantKey { true })
    }

    @Test
    fun `each band reads its own variant key`() {
        assertEquals("2x2", Breakpoints.spec(110f, 110f).variantKey { true })
        assertEquals("2x2", Breakpoints.spec(300f, 150f).variantKey { true })
        assertEquals("4x2", Breakpoints.spec(400f, 200f).variantKey { true })
        assertEquals("4x4", Breakpoints.spec(407f, 412f).variantKey { true })
    }

    @Test
    fun `a custom size falls back to the next larger key that exists`() {
        val only2x2 = { key: String -> key == "2x2" }
        assertEquals("2x2", Breakpoints.spec(300f, 120f).variantKey(only2x2))
        val only4x2 = { key: String -> key == "4x2" }
        assertEquals("4x2", Breakpoints.spec(300f, 120f).variantKey(only4x2))
        assertEquals("4x2", Breakpoints.spec(407f, 412f).variantKey(only4x2))
        assertEquals("2x2", Breakpoints.spec(407f, 412f).variantKey(only2x2))
        // Nothing published per band: the ladder still names a key rather than throwing.
        assertEquals("4x2", Breakpoints.spec(300f, 120f).variantKey { false })
    }

    // --- caps (Task 3) -----------------------------------------------------------

    @Test
    fun `caps grow with the band and the body stays uncapped for the scroll region`() {
        val xs = Breakpoints.spec(300f, 110f)
        assertEquals(1, xs.heroMaxLines)
        assertEquals(0, xs.summaryMaxLines)
        assertEquals(0, xs.bodyMaxLines)
        assertFalse(xs.showsBody)
        assertFalse(xs.showsFooter)

        val s = Breakpoints.spec(300f, 150f)
        assertEquals(1, s.summaryMaxLines)
        assertFalse(s.showsBody)
        assertFalse(s.showsFooter)

        val m = Breakpoints.spec(400f, 250f)
        assertEquals(2, m.heroMaxLines)
        assertEquals(1, m.summaryMaxLines)
        assertEquals(3, m.bodyMaxLines)
        assertTrue(m.showsBody)
        assertTrue(m.showsFooter)
        assertTrue(m.showsRequestAction)

        val l = Breakpoints.spec(400f, 400f)
        assertEquals(8, l.bodyMaxLines)
        assertTrue(l.showsTicker)
        assertTrue(l.showsQuestion)
    }

    @Test
    fun `the width guard keeps a narrow cell single column`() {
        val narrow = Breakpoints.spec(180f, 412f)
        assertTrue(narrow.singleColumn)
        assertEquals(1, narrow.heroMaxLines)     // no split/hero row on a narrow cell
        assertFalse(narrow.showsTicker)
        assertFalse(narrow.showsQuestion)
        assertFalse(narrow.showsHeaderLabel)     // the mark alone identifies the widget

        val wide = Breakpoints.spec(245f, 412f)
        assertFalse(wide.singleColumn)
        assertEquals(2, wide.heroMaxLines)
        assertTrue(wide.showsHeaderLabel)
    }

    // --- band-driven image height (D15) -----------------------------------------

    @Test
    fun `image height is band driven and never exceeds the cell`() {
        assertEquals(96, Breakpoints.spec(300f, 150f).imageHeightDp)
        assertEquals(160, Breakpoints.spec(400f, 250f).imageHeightDp)
        assertEquals(240, Breakpoints.spec(407f, 412f).imageHeightDp)
        // A 4x4 at the 300dp floor still leaves a usable image band.
        val tight = Breakpoints.spec(400f, 300f).imageHeightDp
        assertTrue("tight 4x4 image height was $tight", tight in 120..240)
        // The chrome it subtracts is real: the image can never eat the footer.
        assertTrue(Breakpoints.spec(400f, 412f).imageHeightDp < 412)
    }

    // --- LocalSize plumbing (Task 1) --------------------------------------------

    @Test
    fun `an unspecified LocalSize falls through to the inventory fallback`() {
        assertNull(WidgetSize.fromLocalSize(DpSize.Unspecified))
        assertNull(WidgetSize.fromLocalSize(DpSize(0.dp, 0.dp)))
        assertEquals(407f to 412f, WidgetSize.fromLocalSize(DpSize(407.dp, 412.dp)))
    }

    // --- size classes (Task 11, E2 corners) ------------------------------------

    @Test
    fun `size class matches the canonical ranges`() {
        assertEquals("2x2", WidgetDimensions.sizeClass(109, 56))     // 2x1 floor
        assertEquals("2x2", WidgetDimensions.sizeClass(244, 276))    // 2x2 max, 2 columns
        assertEquals("2x2", WidgetDimensions.sizeClass(200, 150))
        // The guide's ranges overlap at 245-306 x 115-276; the wider reading wins so a
        // cell is never described as smaller than the budget it actually has.
        assertEquals("4x2", WidgetDimensions.sizeClass(306, 276))
        assertEquals("4x2", WidgetDimensions.sizeClass(245, 130))    // 4x1
        assertEquals("4x2", WidgetDimensions.sizeClass(307, 60))     // wide 1-row
        assertEquals("4x2", WidgetDimensions.sizeClass(624, 276))    // 4x2 max
        assertEquals("2x4", WidgetDimensions.sizeClass(180, 400))    // narrow + tall
        assertEquals("4x4", WidgetDimensions.sizeClass(407, 412))    // 4x4 typical
        assertEquals("4x4", WidgetDimensions.sizeClass(624, 422))
        // Outside the guide's ranges entirely.
        assertEquals("custom", WidgetDimensions.sizeClass(700, 300))
        assertEquals("custom", WidgetDimensions.sizeClass(100, 50))
    }

    @Test
    fun `the size class set stays the wire contract's five values`() {
        val classes = setOf(
            WidgetDimensions.sizeClass(110, 110),
            WidgetDimensions.sizeClass(400, 150),
            WidgetDimensions.sizeClass(180, 400),
            WidgetDimensions.sizeClass(400, 400),
            WidgetDimensions.sizeClass(700, 300),
        )
        assertEquals(setOf("2x2", "4x2", "2x4", "4x4", "custom"), classes)
    }
}
