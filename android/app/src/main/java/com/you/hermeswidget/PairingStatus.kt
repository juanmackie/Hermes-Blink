package com.you.hermeswidget

import com.you.hermeswidget.net.ConnectionState

/**
 * What the Settings screen says about pairing, as a pure value.
 *
 * Kept out of the Activity so it can be tested without a device: the indicator is the
 * answer to "is this phone actually paired?", and an answer that is computed in a
 * `onResume` with a `when` buried in a view update is an answer nobody can check.
 */
data class PairingStatus(
    val paired: Boolean,
    val deviceId: String?,
    /** The last thing the phone knew about the connection, from Config. */
    val connection: ConnectionState,
    /** Last successful poll, or null when this phone has never polled. */
    val lastPollAt: Long?,
    /** Last successful fetch of a publication, or null. */
    val lastFetchAt: Long?,
    /** A pairing attempt is in flight right now. */
    val pairingInFlight: Boolean = false,
    val now: Long = System.currentTimeMillis(),
) {
    enum class Tone { PAIRED, WORKING, UNPAIRED, PROBLEM }

    val tone: Tone
        get() = when {
            pairingInFlight -> Tone.WORKING
            !paired -> Tone.UNPAIRED
            connection == ConnectionState.REVOKED || connection == ConnectionState.ERROR -> Tone.PROBLEM
            else -> Tone.PAIRED
        }

    /** One line for the indicator. Deliberately says what is true, not what is hoped. */
    val summary: String
        get() = when (tone) {
            Tone.WORKING -> "Pairing… waiting for the server to answer"
            Tone.UNPAIRED -> "Not paired — enter the code from hermes widget code"
            Tone.PROBLEM -> when (connection) {
                ConnectionState.REVOKED -> "Pairing expired — re-pair this phone"
                ConnectionState.ERROR -> "Connected earlier, now failing — re-pair if this persists"
                else -> "Connection problem — open Diagnostics"
            } + pollSuffix()
            Tone.PAIRED -> buildString {
                append("Paired")
                deviceId?.let { append(" as $it") }
                append(pollSuffix())
            }
        }

    /** A second line only when there is something worth saying beyond the summary. */
    val detail: String?
        get() = when (tone) {
            Tone.PAIRED -> when (connection) {
                ConnectionState.ONLINE -> "Last fetch: ${ageOf(lastFetchAt) ?: "waiting for the first one"}"
                // Never claim there is a cached publication before one has been fetched.
                ConnectionState.OFFLINE -> if (lastFetchAt == null || lastFetchAt <= 0L) {
                    "Offline — nothing has been fetched yet, so the widget has nothing to show"
                } else {
                    "Offline — the widget is showing the publication fetched ${ageOf(lastFetchAt)}"
                }
                else -> null
            }
            Tone.PROBLEM -> "Last poll: ${ageOf(lastPollAt) ?: "never"}"
            else -> null
        }

    private fun pollSuffix(): String {
        val poll = ageOf(lastPollAt) ?: return " · never polled"
        val fetch = ageOf(lastFetchAt) ?: return " · last poll $poll"
        return " · polled $poll · fetched $fetch"
    }

    private fun ageOf(at: Long?): String? {
        if (at == null || at <= 0L) return null
        val seconds = ((now - at).coerceAtLeast(0L)) / 1000L
        return when {
            seconds < 5 -> "moments ago"
            seconds < 60 -> "${seconds}s ago"
            seconds < 3_600 -> "${seconds / 60} min ago"
            seconds < 86_400 -> "${seconds / 3_600} h ago"
            else -> "${seconds / 86_400} d ago"
        }
    }

    companion object {
        /**
         * Read the current truth. `token`/`deviceId` come from the encrypted store, the
         * rest from prefs, so this never touches the network and is safe to call on a
         * 2-second cadence.
         */
        fun read(context: android.content.Context, pairingInFlight: Boolean = false): PairingStatus {
            val store = com.you.hermeswidget.net.SecureStore
            val config = com.you.hermeswidget.net.Config
            val deviceId = store.deviceId(context)
            val times = config.getDiagnosticTimes(context)
            return PairingStatus(
                paired = !deviceId.isNullOrBlank() && !store.token(context).isNullOrBlank(),
                deviceId = deviceId,
                connection = config.getConnectionState(context),
                lastPollAt = times["lastPollAt"],
                lastFetchAt = times["lastFetchAt"],
                pairingInFlight = pairingInFlight,
            )
        }
    }
}
