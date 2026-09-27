package com.you.hermeswidget

import com.you.hermeswidget.net.Config
import org.json.JSONObject

/**
 * What the recorded trail says about a press.
 *
 * There are two producers of outcomes, and they are structurally different:
 *
 *  - the **widget pill**, inside `ActionCallbacks.EventAction.onAction`. Every path
 *    through it also increments the `reached` counter, because that counter is taken on
 *    the first statement of the callback. A `reached` of zero therefore means no press ever
 *    reached the widget.
 *  - the **button in the publication detail view**, an ordinary `Button` in an Activity. It
 *    posts the same event and records the same outcome shape, and it can *never* increment
 *    `reached`, because no Glance action dispatch is involved.
 *
 * So `outcomes > 0 && reached == 0` is the expected steady state of an app with two
 * producers, not a contradiction. The first version of this class asserted the opposite -
 * it treated any outcome at `reached == 0` as impossible - and so Diagnostics spent
 * several rounds reporting the app as broken while it was behaving correctly. Outcomes
 * carry their origin (`Config.SOURCE_*`) so the distinction is data rather than a guess.
 *
 * The genuinely impossible case is narrow and worth keeping: an outcome that the *widget
 * callback* claims to have produced, while the callback's own counter reads zero. That
 * means the record and the counter disagree, and it is still called inconsistent.
 */
data class ActionVerdict(
    val reached: Int,
    /** Outcomes recorded in total, from any producer. */
    val outcomes: Int,
    /** How many of those the Glance callback attributed to itself. */
    val widgetOutcomes: Int,
    val exceptions: Int,
    val lastEvent: String?,
    val lastException: String?,
) {
    val line: String
        get() = when {
            exceptions > 0 ->
                "the widget callback threw $exceptions time(s): " +
                    "${lastException ?: "?"} - our handler, not the press"
            widgetOutcomes > 0 && reached == 0 ->
                "inconsistent: the widget callback recorded $widgetOutcomes outcome(s) but " +
                    "its own counter reads zero"
            reached == 0 && outcomes > 0 ->
                "no press has reached the widget; the $outcomes outcome(s) on record came " +
                    "from the app, which is a different control"
            reached == 0 ->
                "no press has reached the widget, and nothing was recorded anywhere"
            outcomes == 0 ->
                "the widget callback was reached ${reached}x but recorded no outcome - " +
                    "our handler stopped early"
            else ->
                "the widget callback was reached ${reached}x, last " +
                    "'${lastEvent ?: "?"}', $outcomes outcome(s) recorded"
        }

    /** True when a press is known to have got as far as the Glance callback. */
    val pressReachedApp: Boolean get() = reached > 0

    /** True when a press is known *not* to have reached the widget. */
    val pressNeverReachedWidget: Boolean get() = reached == 0

    companion object {
        fun of(reached: JSONObject?, outcomes: List<JSONObject>): ActionVerdict {
            val record = reached ?: JSONObject()
            val widget = outcomes.count { row ->
                row.optString("source") == Config.SOURCE_WIDGET_ACTION
            }
            return ActionVerdict(
                reached = record.optInt("count", 0),
                outcomes = outcomes.size,
                widgetOutcomes = widget,
                exceptions = record.optInt("exceptions", 0),
                lastEvent = record.optString("lastEvent").ifBlank { null },
                lastException = record.optString("lastException").ifBlank { null },
            )
        }
    }
}
