package com.you.hermeswidget.widget

import org.junit.Assert.*
import org.junit.Test

/**
 * Cross-language contract test: Android parser must agree with Python validator
 * via shared fixtures in fixtures/. A failure here breaks the shared v2 contract.
 */
class LayoutContractTest {

    private fun resource(name: String): String {
        // Shared fixtures are on test classpath via sourceSets { resources.srcDir("../fixtures") }
        val stream = javaClass.classLoader!!.getResourceAsStream(name)
            ?: throw IllegalArgumentException("fixture $name not on classpath; check android/app/build.gradle.kts sourceSets")
        return stream.bufferedReader().use { it.readText() }
    }

    @Test fun `valid brief-v2 is accepted`() {
        val json = resource("brief-v2.json")
        val layout = LayoutParser.parse(json)
        assertEquals(2, layout.version)
        assertEquals("hermes-brief", layout.widgetId)
        assertNotNull(layout.updatedAt)
        assertEquals("column", layout.root.type)
    }

    @Test fun `valid minimal is accepted`() {
        val layout = LayoutParser.parse(resource("brief-v2-minimal.json"))
        assertEquals(2, layout.version)
    }

    @Test fun `removed chart type is rejected`() {
        try {
            LayoutParser.parse(resource("invalid-chart.json"))
            fail("chart should be rejected")
        } catch (e: IllegalArgumentException) {
            assertTrue(e.message!!.contains("chart"))
        }
    }

    @Test fun `removed toggle type is rejected`() {
        try {
            LayoutParser.parse(resource("invalid-toggle.json"))
            fail("toggle should be rejected")
        } catch (e: Exception) {
            assertTrue(e.message!!.contains("toggle"))
        }
    }

    @Test fun `removed url action kind is rejected`() {
        try {
            LayoutParser.parse(resource("invalid-url-action.json"))
            fail("url action should be rejected")
        } catch (e: Exception) {
            assertTrue(e.message!!.lowercase().contains("url"))
        }
    }

    @Test fun `calendar month mode is rejected`() {
        try {
            LayoutParser.parse(resource("invalid-calendar-month.json"))
            fail("month mode should be rejected")
        } catch (e: Exception) {
            assertTrue(e.message!!.contains("agenda"))
        }
    }

    @Test fun `brief-v2 preserves item ids and actions`() {
        val layout = LayoutParser.parse(resource("brief-v2.json"))
        val raw = resource("brief-v2.json")
        assertTrue(raw.contains("item-1"))
        assertTrue(raw.contains("\"dismiss\"") || raw.contains("dismiss"))
        assertTrue(raw.contains("\"review\"") || raw.contains("review"))
        // Check that list items with actions exist in parsed model
        fun collectActions(n: Node, out: MutableList<Action>) {
            n.action?.let { out.add(it) }
            n.children?.forEach { collectActions(it, out) }
        }
        val actions = mutableListOf<Action>()
        collectActions(layout.root, actions)
        assertTrue(actions.any { it.kind == "refresh" })
        assertTrue(actions.any { it.kind == "dismiss" || it.kind == "review" })
    }

    @Test fun `brief-v2 has no removed content`() {
        val raw = resource("brief-v2.json")
        for (bad in listOf("\"chart\"", "\"image\"", "\"icon\"", "\"toggle\"", "\"open_app\"")) {
            assertFalse("brief-v2 must not contain $bad", raw.contains(bad))
        }
    }
}
