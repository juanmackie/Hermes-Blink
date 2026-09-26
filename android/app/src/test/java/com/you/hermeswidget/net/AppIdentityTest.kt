package com.you.hermeswidget.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The build identifier is how the server answers "which build rendered this?", so its
 * shape matters more than its cleverness: it must survive a round trip, it must never be
 * invented when the platform cannot tell us, and it must be safe to put on every request.
 */
class AppIdentityTest {

    @Test
    fun `version and build travel as separate headers`() {
        assertEquals("0.2.0", AppIdentity.versionHeader("0.2.0"))
        assertEquals("0.2.0", AppIdentity.versionHeader("  0.2.0  "))
        assertEquals("200", AppIdentity.buildHeader(200L))
    }

    @Test
    fun `nothing to report means no header rather than a lie`() {
        assertNull(AppIdentity.versionHeader(null))
        assertNull(AppIdentity.versionHeader(""))
        assertNull(AppIdentity.versionHeader("x".repeat(64)))
        assertNull(AppIdentity.buildHeader(null))
        assertNull(AppIdentity.buildHeader(-1L))
        assertNull(AppIdentity.buildHeader(Long.MAX_VALUE))
        assertFalse(AppIdentity.Info(null, null, 34).isReportable)
        assertTrue(AppIdentity.Info("0.2.0", null, 34).isReportable)
        assertTrue(AppIdentity.Info(null, 200L, 34).isReportable)
    }

    @Test
    fun `the client block is what the server parses`() {
        val json = AppIdentity.clientBlock("0.2.0", 200L, 34)
        assertEquals("0.2.0", json.getString("appVersion"))
        assertEquals(200L, json.getLong("appBuildCode"))
        assertEquals(34, json.getInt("osSdk"))

        val empty = AppIdentity.clientBlock(null, null, 34)
        assertTrue(empty.isNull("appVersion"))
        assertTrue(empty.isNull("appBuildCode"))
        // The OS level is always known, so it is always present.
        assertEquals(34, empty.getInt("osSdk"))
    }

    @Test
    fun `describe reads like a human sentence`() {
        assertEquals("0.2.0 (build 200)", AppIdentity.describe(AppIdentity.Info("0.2.0", 200L, 34)))
        assertEquals("0.2.0", AppIdentity.describe(AppIdentity.Info("0.2.0", null, 34)))
        assertEquals("build 200", AppIdentity.describe(AppIdentity.Info(null, 200L, 34)))
        assertEquals("unknown build", AppIdentity.describe(AppIdentity.Info(null, null, 34)))
    }
}
