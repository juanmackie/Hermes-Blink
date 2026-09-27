package com.you.hermeswidget

/**
 * What the recorded trail says about a press.
 *
 * Round 13: the "did the press fire?" counter was maintained in a receiver that never
 * sees a Glance action broadcast, so it read zero and that zero was read as evidence. A
 * number that cannot move is worse than no number, so the counter now lives where the
 * dispatch arrives and is named for what it measures: *reached*.
 *
 * The verdict is a pure function so the sentence a human reads is a thing that can be
 * tested, and so the two possible failures cannot be quietly conflated again:
 *
 *  - reached advanced, an outcome exists: the callback ran; anything wrong is in our handler.
 *  - reached did not advance, no outcome: the press never got past the touch. That is a
 *    layout or launcher question, not a request-path question.
 *  - a callback exception: the handler threw, and says so.
 *  - an outcome with reached at zero is impossible and is reported as inconsistent rather
 *    than being smoothed over.
 */
data class ActionVerdict(
    val reached: Int,
    val outcomes: Int,
    val exceptions: Int,
    val lastEvent: String?,
    val lastException: String?,
) {
    val line: String
        get() = when {
            exceptions > 0 ->
                "callback threw ${exceptions} time(s): ${lastException ?: "?"} — the handler, not the press"
            reached == 0 && outcomes > 0 ->
                "inconsistent: $outcomes outcome(s) but the callback was never reached"
            reached == 0 ->
                "no press reached the callback: either none was made, or the touch did not " +
                    "get past the surface"
            outcomes == 0 ->
                "callback reached ${reached}x but recorded no outcome — the handler stopped early"
            else ->
                "callback reached ${reached}x, last '${lastEvent ?: "?"}', $outcomes outcome(s) recorded"
        }

    /** True when the press is known to have got as far as our code. */
    val pressReachedApp: Boolean get() = reached > 0

    companion object {
        fun of(reached: org.json.JSONObject?, outcomes: Int): ActionVerdict {
            val record = reached ?: org.json.JSONObject()
            return ActionVerdict(
                reached = record.optInt("count", 0),
                outcomes = outcomes,
                exceptions = record.optInt("exceptions", 0),
                lastEvent = record.optString("lastEvent").ifBlank { null },
                lastException = record.optString("lastException").ifBlank { null },
            )
        }
    }
}
