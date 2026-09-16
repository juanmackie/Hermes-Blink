package com.you.hermeswidget.net

import java.net.HttpURLConnection
import java.net.URL

object HermesApi {
    fun health(baseUrl: String): Pair<Int, String> {
        val url = URL(baseUrl.trimEnd('/') + "/v1/health")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "GET"
        conn.connectTimeout = 5000
        conn.readTimeout = 5000
        val code = conn.responseCode
        val body = if (code == 200) conn.inputStream.bufferedReader().use { it.readText() } else ""
        return code to body
    }

    fun fetchWidget(baseUrl: String, widgetId: String, token: String?): Pair<Int, String?> {
        val url = URL(baseUrl.trimEnd('/') + "/v1/widgets/$widgetId")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "GET"
        conn.connectTimeout = 10000
        conn.readTimeout = 10000
        if (!token.isNullOrEmpty()) {
            conn.setRequestProperty("Authorization", "Bearer $token")
        }
        return try {
            val code = conn.responseCode
            val body = if (code == 200) conn.inputStream.bufferedReader().use { it.readText() } else null
            code to body
        } catch (e: Exception) {
            -1 to ("Error: ${e.message}")
        }
    }

    fun mintPairingCode(baseUrl: String, agentToken: String): Pair<Int, String?> {
        val url = URL(baseUrl.trimEnd('/') + "/v1/pairing-codes")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.connectTimeout = 10000
        conn.readTimeout = 10000
        conn.setRequestProperty("Authorization", "Bearer $agentToken")
        return try {
            val c = conn.responseCode
            val b = if (c in 200..299) conn.inputStream.bufferedReader().use { it.readText() } else null
            c to b
        } catch (e: Exception) { -1 to ("Error: ${e.message}") }
    }

    fun pair(baseUrl: String, code: String): Pair<Int, String?> {
        val url = URL(baseUrl.trimEnd('/') + "/v1/pair")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        conn.connectTimeout = 10000
        conn.readTimeout = 10000
        val body = """{"code":"$code"}"""
        conn.outputStream.write(body.toByteArray())
        return try {
            val c = conn.responseCode
            val b = if (c in 200..299) conn.inputStream.bufferedReader().use { it.readText() } else null
            c to b
        } catch (e: Exception) {
            -1 to ("Error: ${e.message}")
        }
    }

    fun fetchWidgets(baseUrl: String, token: String): Pair<Int, String?> {
        val url = URL(baseUrl.trimEnd('/') + "/v1/widgets")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "GET"
        conn.connectTimeout = 5000
        conn.readTimeout = 5000
        if (!token.isNullOrEmpty()) conn.setRequestProperty("Authorization", "Bearer $token")
        return try {
            val code = conn.responseCode
            val body = if (code == 200) conn.inputStream.bufferedReader().use { it.readText() } else null
            code to body
        } catch (e: Exception) { -1 to ("Error: ${e.message}") }
    }

    fun postEvent(baseUrl: String, widgetId: String, eventName: String, payload: String?, token: String?): Pair<Int, String?> {
        val url = URL(baseUrl.trimEnd('/') + "/v1/widgets/$widgetId/events")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        conn.connectTimeout = 5000
        conn.readTimeout = 5000
        if (!token.isNullOrEmpty()) conn.setRequestProperty("Authorization", "Bearer $token")
        val payloadJson = payload ?: "{}"
        val fullPayload = """{"event":"$eventName","payload":$payloadJson}"""
        conn.outputStream.write(fullPayload.toByteArray())
        return try {
            val code = conn.responseCode
            val body = if (code in 200..299) conn.inputStream.bufferedReader().use { it.readText() } else null
            code to body
        } catch (e: Exception) { -1 to ("Error: ${e.message}") }
    }
}
