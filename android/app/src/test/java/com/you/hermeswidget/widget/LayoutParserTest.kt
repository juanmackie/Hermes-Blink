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
}
