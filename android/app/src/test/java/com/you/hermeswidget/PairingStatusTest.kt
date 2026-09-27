package com.you.hermeswidget

import com.you.hermeswidget.net.ConnectionState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Settings pairing indicator, tested without a device.
 *
 * This is the part a user watches while pairing, and it is the part that was missing: the
 * screen used to report nothing at all until a toast appeared and disappeared. The
 * indicator has to be right about the awkward cases especially — a revoked pairing and a
 * still-running attempt look very different from each other, and both look like
 * "not paired" if you only check for a token.
 */
class PairingStatusTest {

    private val now = 1_700_000_000_000L

    private fun status(
        paired: Boolean = true,
        deviceId: String? = "dvc_abc123",
        connection: ConnectionState = ConnectionState.ONLINE,
        lastPollAt: Long? = now - 30_000,
        lastFetchAt: Long? = now - 25_000,
        lastCheckedAt: Long? = now - 27_000,
        content: PairingStatus.Content? = null,
        pairingInFlight: Boolean = false,
    ) = PairingStatus(
        paired = paired,
        deviceId = deviceId,
        connection = connection,
        lastPollAt = lastPollAt,
        lastFetchAt = lastFetchAt,
        lastCheckedAt = lastCheckedAt,
        content = content,
        pairingInFlight = pairingInFlight,
        now = now,
    )

    @Test
    fun `an unpaired phone says so and suggests the next step`() {
        val s = status(paired = false, deviceId = null, connection = ConnectionState.UNPAIRED)
        assertEquals(PairingStatus.Tone.UNPAIRED, s.tone)
        assertTrue(s.summary.contains("Not paired"))
        assertTrue(s.summary.contains("hermes widget code"))
        assertNull(s.detail)
    }

    @Test
    fun `a healthy pairing names the device and how fresh it is`() {
        val s = status()
        assertEquals(PairingStatus.Tone.PAIRED, s.tone)
        assertTrue(s.summary.contains("Paired as dvc_abc123"))
        assertTrue(s.summary, s.summary.contains("polled 30s ago"))
        assertTrue(s.summary, s.summary.contains("new content 25s ago"))
        assertTrue(s.detail!!, s.detail!!.contains("Last new content"))
    }

    @Test
    fun `being asked is not the same as receiving`() {
        // Round 15: a 304 was recorded as a fetch, so this screen said "Last fetch 26s
        // ago" while the widget said the publication had expired. Both were true and the
        // two numbers contradicted each other.
        val s = status(lastFetchAt = now - 4 * 3_600_000, lastCheckedAt = now - 26_000)
        assertTrue(s.summary, s.summary.contains("polled 30s ago"))
        assertTrue(s.summary, s.summary.contains("new content 4 h ago"))

        val neverNew = status(lastFetchAt = null, lastCheckedAt = now - 26_000)
        assertTrue(
            neverNew.summary,
            neverNew.summary.contains("nothing new; asked 26s ago"),
        )
        assertTrue(
            "with no new content the summary must not claim a fetch",
            !neverNew.summary.contains("new content"),
        )
    }

    @Test
    fun `expired content reads as waiting on Hermes, not as a fault`() {
        val s = status(
            content = PairingStatus.Content(ageMillis = 4 * 3_600_000, expired = true, revision = 24),
            lastFetchAt = null, lastCheckedAt = now - 26_000,
        )
        assertEquals(PairingStatus.Tone.WAITING, s.tone)
        assertTrue(s.summary, s.summary.contains("waiting on Hermes"))
        assertTrue(s.summary, s.summary.contains("revision 24"))
        assertTrue(s.summary, s.summary.contains("expired 4 h ago"))
        assertTrue("no doubled unit", !s.summary.contains("ago ago"))
        assertTrue(
            s.detail!!,
            s.detail!!.contains("healthy") && s.detail!!.contains("publishes"),
        )
    }

    @Test
    fun `a revoked pairing is a problem, not simply unpaired`() {
        val s = status(connection = ConnectionState.REVOKED)
        assertEquals(PairingStatus.Tone.PROBLEM, s.tone)
        assertTrue(s.summary, s.summary.contains("Pairing expired"))
        assertTrue(s.summary, s.summary.contains("re-pair"))
    }

    @Test
    fun `an attempt in flight wins over everything else`() {
        val s = status(pairingInFlight = true)
        assertEquals(PairingStatus.Tone.WORKING, s.tone)
        assertTrue(s.summary.contains("Pairing"))
    }

    @Test
    fun `a never-polled phone says so`() {
        val s = status(lastPollAt = null, lastFetchAt = null, lastCheckedAt = null)
        assertTrue(s.summary, s.summary.contains("never polled"))
    }

    @Test
    fun `offline is not a problem state, and says what the user will see`() {
        val s = status(connection = ConnectionState.OFFLINE, lastFetchAt = null)
        assertEquals(PairingStatus.Tone.PAIRED, s.tone)
        assertTrue(s.detail!!, s.detail!!.contains("Offline"))
        // Polled but nothing fetched: never claim a cached publication exists.
        assertTrue(s.summary, !s.summary.contains("fetched"))
        assertTrue(s.detail!!, s.detail!!.contains("nothing to show"))

        val withCache = status(connection = ConnectionState.OFFLINE)
        assertTrue(
            withCache.detail!!,
            withCache.detail!!.contains("showing the publication fetched 25s ago"),
        )
    }

    @Test
    fun `a phone that has never polled does not claim a freshness it does not have`() {
        val s = status(lastPollAt = null, lastFetchAt = null)
        assertTrue(s.summary, s.summary.contains("never polled"))
        assertTrue(s.summary, !s.summary.contains("fetched"))
    }

    @Test
    fun `ages are human and bounded, never negative or absurd`() {
        assertTrue(status(lastPollAt = now - 1_000).summary.contains("moments"))
        assertTrue(status(lastPollAt = now - 600_000).summary.contains("10 min"))
        assertTrue(status(lastPollAt = now - 7_200_000).summary.contains("2 h"))
        assertTrue(status(lastPollAt = now - 172_800_000).summary.contains("2 d"))
        // A clock that moved backwards must not render a negative age.
        assertTrue(status(lastPollAt = now + 60_000).summary.contains("moments"))
        // Zero is "never", not 1970.
        assertTrue(status(lastPollAt = 0L).summary.contains("never polled"))
    }
}
