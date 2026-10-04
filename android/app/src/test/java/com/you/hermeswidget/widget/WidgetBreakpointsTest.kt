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

    private companion object {
        /** Below this the cell cannot hold the header plus a readable row at all. */
        const val MIN_USABLE_HEIGHT_DP = 60f
    }


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
        assertEquals(0, Breakpoints.spec(300f, 150f).imageHeightDp)
        assertEquals(68, Breakpoints.spec(400f, 250f).imageHeightDp)
        assertEquals(211, Breakpoints.spec(407f, 412f).imageHeightDp)
        // A 4x4 at the 300dp floor still leaves a usable image band.
        val tight = Breakpoints.spec(400f, 300f).imageHeightDp
        assertTrue("tight 4x4 image height was $tight", tight in 0..240)
        // The chrome it subtracts is real: the image can never eat the footer.
        assertTrue(Breakpoints.spec(400f, 412f).imageHeightDp < 412)
    }

    // --- pinned chrome must fit (round 6) --------------------------------------

    @Test
    fun `the scroll region and the pinned chrome always fit the instance`() {
        // The footer is the only way to poke the agent from the widget, so the column's
        // total height must never exceed the cell: a weight-constrained lazy list is
        // measured by the platform, and a miss pushes the footer out of view.
        val heights = listOf(56f, 110f, 130f, 184f, 185f, 250f, 276f, 300f, 412f)
        for (height in heights) {
            for (width in listOf(110f, 245f, 407f, 624f)) {
                val spec = Breakpoints.spec(width, height)
                val total = spec.chromeHeightDp + spec.scrollHeightDp
                assertTrue(
                    "width=${width}dp height=${height}dp: chrome ${spec.chromeHeightDp} + " +
                        "scroll ${spec.scrollHeightDp} = $total exceeds the cell",
                    total <= height || height < MIN_USABLE_HEIGHT_DP,
                )
                assertTrue("scroll region must never be zero", spec.scrollHeightDp > 0)
            }
        }
    }

    @Test
    fun `a tall instance gets the height the chrome left over`() {
        val spec = Breakpoints.spec(407f, 412f)
        // 412 - (24 padding + 52 header-with-action + 18 status line) = 318
        assertEquals(318, spec.scrollHeightDp)
        assertEquals(12f + 12f + 52f + 18f, spec.chromeHeightDp, 0.01f)
    }

    @Test
    fun `the action is pinned in the header, so it reserves its own height there`() {
        // Round 11: the action moved from below the scroll region to above it. The chrome
        // arithmetic has to follow, or the list is measured against a header that is now
        // 48dp taller and the column overflows the cell again.
        val withAction = Breakpoints.spec(407f, 270f)
        assertTrue(withAction.showsRequestAction)
        assertEquals(12f + 12f + 52f + 18f, withAction.chromeHeightDp, 0.01f)
        // 270 - 94 = 176
        assertEquals(176, withAction.scrollHeightDp)
        assertTrue(withAction.chromeHeightDp + withAction.scrollHeightDp <= 270f)

        // A band with no action keeps the short header.
        val noAction = Breakpoints.spec(300f, 120f)
        assertFalse(noAction.showsRequestAction)
        assertEquals(12f * 2 + 20f, noAction.chromeHeightDp, 0.01f)
    }

    @Test
    fun `a 2x2 cell still fits the header action and a readable list`() {
        for (height in listOf(185f, 200f, 250f, 276f)) {
            val spec = Breakpoints.spec(300f, height)
            assertTrue("band=${spec.band}", spec.showsRequestAction)
            assertTrue(
                "height=$height: ${spec.chromeHeightDp}+${spec.scrollHeightDp} overflows",
                spec.chromeHeightDp + spec.scrollHeightDp <= height,
            )
        }
    }

    @Test
    fun `a band with no footer reserves only the header and the padding`() {
        val spec = Breakpoints.spec(300f, 120f)   // S: no footer
        assertFalse(spec.showsFooter)
        // Only the padding and the header are reserved: 12 + 12 + 20 = 44.
        assertEquals(12f * 2 + 20f, spec.chromeHeightDp, 0.01f)
        assertEquals(120 - 44, spec.scrollHeightDp)
    }

    @Test
    fun `a cell too short for real chrome yields a short list rather than an overflow`() {
        // 56dp (the 2x1 floor) cannot fit a header and a body. Guaranteeing a minimum
        // list height here is what pushed 412dp of content into a 270dp cell in round 8,
        // so fitting wins: the list gets what is left, and says it has no room.
        val spec = Breakpoints.spec(300f, 56f)
        // 56 - (24 padding + 20 header) = 12dp of list, and it fits exactly.
        assertEquals(12, spec.scrollHeightDp)
        assertFalse(spec.hasReadableBody)
        assertEquals(56f, spec.chromeHeightDp + spec.scrollHeightDp, 0.01f)

        // From the 4x2 floor upward the list is genuinely readable.
        val floor = Breakpoints.spec(300f, 130f)
        assertTrue(floor.hasReadableBody)
    }

    // --- the round-8 P0: compose for the cell, not for the responsive sample -----

    @Test
    fun `the instance report wins over the responsive sample`() {
        // The exact failure from the field: a 407x270dp cell whose nearest sample in the
        // responsive set was 407x412, so 412dp of content was composed into 270dp and the
        // pinned action fell outside the cell. No broadcast, nothing on the server.
        val (spec, geometry) = SizeGate.spec(
            instanceDp = 407f to 270f,
            sampleDp = 407f to 412f,
        )
        assertEquals(SizeGate.Geometry.Source.INSTANCE_INVENTORY, geometry.source)
        assertEquals(270f, geometry.heightDp, 0.01f)
        assertEquals(WidgetBand.M, spec.band)
    }

    @Test
    fun `composed height equals the cell height at every reported size`() {
        // The acceptance criterion: for any cell, what we compose for is the cell.
        val cells = listOf(
            110f to 56f, 306f to 110f, 200f to 200f, 407f to 270f,
            624f to 130f, 407f to 412f, 624f to 422f, 1220f to 300f,
        )
        for ((width, height) in cells) {
            // Whatever sample Glance happened to compose for, from anywhere in the set.
            for (sample in listOf(110f to 56f, 407f to 412f, 624f to 422f, 306f to 276f)) {
                val (spec, geometry) = SizeGate.spec(width to height, sample)
                val total = spec.chromeHeightDp + spec.scrollHeightDp
                assertTrue(
                    "cell ${width}x$height with sample $sample: composed $total exceeds " +
                        "the cell ($height)",
                    total <= height + 1f,
                )
                assertEquals(
                    "the band must be the one the reported geometry implies",
                    Breakpoints.spec(width, height).band,
                    spec.band,
                )
            }
        }
    }

    @Test
    fun `the band is derived from the reported geometry, never from the sample`() {
        val samples = listOf(110f to 56f, 624f to 422f, 407f to 412f)
        for (width in listOf(110f, 300f, 407f, 624f)) {
            for (height in listOf(60f, 120f, 270f, 400f)) {
                val bands = samples.map { sample ->
                    SizeGate.spec(width to height, sample).first.band
                }
                assertEquals(
                    "band varied with the sample for a ${width}x$height cell",
                    1,
                    bands.distinct().size,
                )
            }
        }
    }

    @Test
    fun `the sample is only a hint, and the fallback is last`() {
        val withSample = SizeGate.spec(null, 407f to 412f)
        assertEquals(SizeGate.Geometry.Source.RESPONSIVE_SAMPLE, withSample.second.source)
        val withNothing = SizeGate.spec(null, null)
        assertEquals(SizeGate.Geometry.Source.FALLBACK, withNothing.second.source)
        assertEquals(SizeGate.FALLBACK_HEIGHT_DP, withNothing.second.heightDp, 0.01f)
        // And the narrowest real cell still gets a usable, fitting layout.
        assertTrue(withNothing.first.scrollHeightDp > 0)
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

