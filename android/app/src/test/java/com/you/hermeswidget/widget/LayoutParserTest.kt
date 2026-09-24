package com.you.hermeswidget.widget

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class LayoutParserTest {

    @Test
    fun `node without id or label does not throw`() {
        // Regression: optString("id", null).takeIf { it.isNotEmpty() } NPE'd here.
        val layout = LayoutParser.parse("""{"root":{"type":"column"}}""")
        assertEquals("column", layout.root.type)
        assertNull(layout.root.id)
        assertNull(layout.root.label)
    }

    @Test
    fun `parses nested children and optional fields`() {
        val json = """
            {"root":{"type":"column","children":[
              {"type":"text","value":"Good morning","style":"title"},
              {"type":"button","label":"Refresh","action":{"kind":"event","event":"refresh_briefing"}},
              {"type":"row","children":[{"type":"text","text":"fallback"}]}
            ]}}
        """
        val root = LayoutParser.parse(json).root
        val kids = root.children!!
        assertEquals(3, kids.size)
        assertEquals("Good morning", kids[0].value)
        assertEquals("Refresh", kids[1].label)
        assertEquals("event", kids[1].action!!.kind)
        assertEquals("refresh_briefing", kids[1].action!!.event)
        // "text" key is accepted as a fallback for "value"
        assertEquals("fallback", kids[2].children!![0].value)
    }

    @Test
    fun `missing root is rejected`() {
        try {
            LayoutParser.parse("{}")
            fail("expected IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            assertTrue(e.message!!.contains("root"))
        }
    }

    @Test
    fun `alignment and padding survive parsing`() {
        // Both were declared in layout.schema.json but dropped by the parser before v2.1,
        // so the renderer could never honour them.
        val json = """
            {"root":{"type":"column","alignment":"center",
              "padding":{"top":16,"bottom":16,"start":16,"end":16},
              "children":[{"type":"text","value":"hi","alignment":"end"}]}}
        """
        val root = LayoutParser.parse(json).root
        assertEquals("center", root.alignment)
        assertEquals(16, root.padding!!.top)
        assertEquals(16, root.padding!!.start)
        assertEquals("end", root.children!![0].alignment)
    }

    @Test
    fun `a node without padding has no padding object`() {
        val root = LayoutParser.parse("""{"root":{"type":"column"}}""").root
        assertNull(root.padding)
        assertNull(root.alignment)
    }

    @Test
    fun `every style field the shared fixture declares is parsed`() {
        // fixtures/brief-v2.json exercises style, color, spacing, thickness, weight, maxItems.
        val stream = javaClass.classLoader!!.getResourceAsStream("brief-v2.json")!!
        val root = LayoutParser.parse(stream.bufferedReader().use { it.readText() }).root
        assertEquals(12, root.spacing)
        val divider = root.children!!.first { it.type == "divider" }
        assertEquals(1, divider.thickness)
        assertEquals("#e0e0e0", divider.color)
        val row = root.children!!.first { it.type == "row" }
        assertEquals(16, row.spacing)
        assertEquals(1.0, row.children!!.first { it.weight != null }.weight!!, 0.0)
        val calendar = root.children!!.first { it.type == "calendar" }
        assertEquals(3, calendar.maxItems)
        assertEquals("#7C3AED", calendar.events!![1].color)
        assertEquals("title", root.children!![0].style)
    }
}
