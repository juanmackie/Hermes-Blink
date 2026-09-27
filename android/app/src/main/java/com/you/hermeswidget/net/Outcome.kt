package com.you.hermeswidget.net

import org.json.JSONObject

/**
 * What actually happened when the user pressed a widget button.
 *
 * Field round 5: every failure showed the same toast ("Update request unavailable"), so
 * a 403 (token is not a device), a 401 (needs re-pairing), a 400 (server rejected the
 * body), a 429 (rate limited) and a network abort were indistinguishable — to the user,
 * and to whoever was reading the server. The three silent `?: return` exits in the tap
 * path were worse still: they looked identical to "the tap did nothing".
 *
 * This turns an [HttpResult] into one honest sentence, and it never invents success.
 */
data class Outcome(
    val ok: Boolean,
    /** Short code for logs and the server's own error field, e.g. "device_required". */
    val code: String,
    /** One sentence for the user. */
    val message: String,
    val httpStatus: Int? = null,
) {
    companion object {
        /** The two ways a tap can fail before it ever reaches the network. */
        const val CODE_NO_SERVER = "no_server_url"
        const val CODE_NO_TOKEN = "no_device_token"
        const val CODE_OFFLINE = "network_error"

        fun from(result: HttpResult, success: String, failurePrefix: String = ""): Outcome {
            if (result.code in 200..299) return Outcome(true, "ok", success, result.code)
            // The server's own machine-readable code, when it sent one.
            val serverCode = runCatching {
                result.body?.takeIf { it.isNotBlank() }
                    ?.let { JSONObject(it).optString("error") }
                    ?.takeIf { it.isNotEmpty() }
            }.getOrNull()
            val detail = runCatching {
                result.body?.takeIf { it.isNotBlank() }?.let { JSONObject(it).optString("detail") }
            }.getOrNull()
            return Outcome(
                ok = false,
                code = serverCode ?: when (result.code) {
                    401 -> "unauthorized"
                    403 -> "forbidden"
                    404 -> "not_found"
                    429 -> "rate_limited"
                    in 500..599 -> "server_error"
                    -1 -> CODE_OFFLINE
                    else -> "http_${result.code}"
                },
                message = explain(
                    result.code, serverCode, detail, failurePrefix,
                ),
                httpStatus = result.code.takeIf { it > 0 },
            )
        }

        private fun explain(status: Int, serverCode: String?, detail: String?, prefix: String): String {
            val lead = if (prefix.isBlank()) "" else "$prefix: "
            return when {
                status == -1 -> "${lead}no connection to Hermes (${CODE_OFFLINE})"
                status == 401 -> "${lead}this phone is not authorised — re-pair it in the app"
                status == 403 -> "${lead}Hermes refused this device (${
                    serverCode ?: "forbidden"
                }) — re-pair it in the app"
                status == 404 -> "${lead}Hermes does not know this widget (${serverCode ?: "not_found"})"
                status == 429 -> "${lead}too many requests — try again shortly"
                status in 500..599 -> "${lead}Hermes had a server error ($status)"
                else -> {
                    val code = serverCode ?: "http_$status"
                    val extra = detail?.takeIf { it.isNotBlank() && it.length <= 120 }?.let { ": $it" } ?: ""
                    "${lead}Hermes rejected the request ($code)$extra"
                }
            }
        }

        fun noServer(): Outcome = Outcome(
            ok = false,
            code = CODE_NO_SERVER,
            message = "no Hermes server is configured on this phone",
        )

        fun noToken(): Outcome = Outcome(
            ok = false,
            code = CODE_NO_TOKEN,
            message = "this phone is not paired — pair it in the app",
        )
    }
}
