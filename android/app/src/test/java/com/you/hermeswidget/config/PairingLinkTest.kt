package com.you.hermeswidget.config

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PairingLinkTest {
    @Test
    fun parsesValidLink() {
        assertEquals(
            "https://widget.example.ts.net:8443" to "K7QP-3M2X",
            PairingLink.parse(
                "hermeswidget://pair?url=https%3A%2F%2Fwidget.example.ts.net%3A8443&code=K7QP-3M2X"
            ),
        )
    }

    @Test
    fun roundTripsThroughLink() {
        val link = PairingLink.link("https://widget.example.ts.net:8443", "K7QP-3M2X")
        assertEquals("https://widget.example.ts.net:8443" to "K7QP-3M2X", PairingLink.parse(link))
    }

    @Test
    fun rejectsPlaintextAndLoopback() {
        assertNull(
            PairingLink.parse(
                "hermeswidget://pair?url=http%3A%2F%2F127.0.0.1%3A8788&code=K7QP-3M2X"
            )
        )
    }

    @Test
    fun rejectsMissingOrMalformedParts() {
        assertNull(PairingLink.parse(null))
        assertNull(PairingLink.parse(""))
        assertNull(PairingLink.parse("hermeswidget://pair?url=https%3A%2F%2Fhost"))
        assertNull(PairingLink.parse("https://host/pair?code=K7QP-3M2X"))
        assertNull(PairingLink.parse("hermeswidget://pair?url=https%3A%2F%2Fhost&code=short"))
    }
}
