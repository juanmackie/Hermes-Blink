package com.you.hermeswidget.net

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.nio.charset.StandardCharsets

data class HttpResult(
    val code: Int,
    val body: String? = null,
    val bytes: ByteArray? = null,
    val etag: String? = null,
    val retryAfterSeconds: Int? = null,
    val error: String? = null,
)

object HermesApi {
    private const val MAX_PUBLICATION_BYTES = 512 * 1024
    private const val MAX_ASSET_BYTES = 5 * 1024 * 1024
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

    fun pair(baseUrl: String, code: String, deviceLabel: String? = null): Pair<Int, String?> {
        val url = URL(baseUrl.trimEnd('/') + "/v1/pair")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        conn.connectTimeout = 10000
        conn.readTimeout = 10000
        val body = JSONObject().put("code", code).apply {
            // A default label makes multiple devices distinguishable in the host CLI.
            if (!deviceLabel.isNullOrBlank()) put("deviceLabel", deviceLabel)
        }.toString()
        conn.outputStream.write(body.toByteArray(StandardCharsets.UTF_8))
        return try {
            val c = conn.responseCode
            val b = if (c in 200..299) conn.inputStream.bufferedReader().use { it.readText() } else null
            c to b
        } catch (e: Exception) {
            -1 to ("Error: ${e.message}")
        }
    }

    fun renameDevice(baseUrl: String, token: String, label: String): HttpResult {
        val conn = open(baseUrl, "/v1/device", token, method = "PATCH")
        return try {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.write(
                JSONObject().put("label", label).toString().toByteArray(StandardCharsets.UTF_8)
            )
            val code = conn.responseCode
            HttpResult(
                code = code,
                body = if (code in 200..299) {
                    conn.inputStream.bufferedReader().use { it.readText() }
                } else {
                    null
                },
                retryAfterSeconds = retryAfter(conn),
            )
        } catch (e: Exception) {
            HttpResult(-1, error = e.message ?: e.javaClass.simpleName)
        } finally {
            conn.disconnect()
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

    fun fetchPublication(
        baseUrl: String,
        widgetId: String,
        token: String,
        etag: String? = null,
    ): HttpResult {
        val conn = open(baseUrl, "/v1/widgets/${pathSegment(widgetId)}/publication", token, etag)
        return try {
            val code = conn.responseCode
            when (code) {
                200 -> HttpResult(
                    code = code,
                    body = conn.inputStream.use { readText(it, MAX_PUBLICATION_BYTES) },
                    etag = conn.getHeaderField("ETag"),
                    retryAfterSeconds = retryAfter(conn),
                )
                304 -> HttpResult(code, etag = conn.getHeaderField("ETag"))
                else -> HttpResult(code, retryAfterSeconds = retryAfter(conn))
            }
        } catch (e: Exception) {
            HttpResult(-1, error = e.message ?: e.javaClass.simpleName)
        } finally {
            conn.disconnect()
        }
    }

    fun fetchAsset(
        baseUrl: String,
        assetId: String,
        token: String,
        etag: String? = null,
    ): HttpResult {
        val conn = open(baseUrl, "/v1/assets/${pathSegment(assetId)}", token, etag)
        return try {
            val code = conn.responseCode
            when (code) {
                200 -> HttpResult(
                    code = code,
                    bytes = conn.inputStream.use { readBytes(it, MAX_ASSET_BYTES) },
                    etag = conn.getHeaderField("ETag"),
                    retryAfterSeconds = retryAfter(conn),
                )
                304 -> HttpResult(code, etag = conn.getHeaderField("ETag"))
                else -> HttpResult(code, retryAfterSeconds = retryAfter(conn))
            }
        } catch (e: Exception) {
            HttpResult(-1, error = e.message ?: e.javaClass.simpleName)
        } finally {
            conn.disconnect()
        }
    }

    fun acknowledgeRender(
        baseUrl: String,
        widgetId: String,
        token: String,
        revision: Int,
        width: Int,
        height: Int,
    ): HttpResult {
        val conn = open(
            baseUrl,
            "/v1/widgets/${pathSegment(widgetId)}/publication/ack",
            token,
            method = "POST",
        )
        return try {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            val body = JSONObject()
                .put("revision", revision)
                .put("status", "render_submitted")
                .put("renderedWidth", width)
                .put("renderedHeight", height)
                .toString()
            conn.outputStream.write(body.toByteArray(StandardCharsets.UTF_8))
            val code = conn.responseCode
            HttpResult(
                code = code,
                body = if (code in 200..299) {
                    conn.inputStream.bufferedReader().use { it.readText() }
                } else {
                    null
                },
                retryAfterSeconds = retryAfter(conn),
            )
        } catch (e: Exception) {
            HttpResult(-1, error = e.message ?: e.javaClass.simpleName)
        } finally {
            conn.disconnect()
        }
    }

    fun postEvent(
        baseUrl: String,
        widgetId: String,
        eventName: String,
        payload: String?,
        token: String?,
    ): Pair<Int, String?> {
        val result = postEventResult(baseUrl, widgetId, eventName, payload, token, null, null, null, false)
        return result.code to result.body
    }

    fun postEventWithFields(
        baseUrl: String,
        widgetId: String,
        eventName: String,
        fields: JSONObject,
        token: String,
    ): HttpResult {
        val conn = open(baseUrl, "/v1/widgets/${pathSegment(widgetId)}/events", token, method = "POST")
        return try {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            val body = JSONObject().put("event", eventName)
            val keys = fields.keys()
            while (keys.hasNext()) {
                val key = keys.next()
                body.put(key, fields.get(key))
            }
            conn.outputStream.write(body.toString().toByteArray(StandardCharsets.UTF_8))
            val code = conn.responseCode
            HttpResult(
                code = code,
                body = if (code in 200..299) conn.inputStream.bufferedReader().use { it.readText() } else null,
                retryAfterSeconds = retryAfter(conn),
            )
        } catch (e: Exception) {
            HttpResult(-1, error = e.message ?: e.javaClass.simpleName)
        } finally {
            conn.disconnect()
        }
    }

    fun postAction(
        baseUrl: String,
        widgetId: String,
        eventName: String,
        itemId: String,
        actionClass: String,
        revision: Int,
        clientEventId: String,
        confirmOnDevice: Boolean,
        token: String,
        payloadJson: String? = null,
    ): HttpResult = postEventResult(
        baseUrl, widgetId, eventName, null, token, itemId, actionClass,
        revision, confirmOnDevice, clientEventId, payloadJson,
    )

    private fun postEventResult(
        baseUrl: String,
        widgetId: String,
        eventName: String,
        payload: String?,
        token: String?,
        itemId: String? = null,
        actionClass: String? = null,
        revision: Int? = null,
        confirmOnDevice: Boolean = false,
        clientEventId: String? = null,
        actionPayload: String? = null,
    ): HttpResult {
        val conn = open(baseUrl, "/v1/widgets/${pathSegment(widgetId)}/events", token ?: "", method = "POST")
        return try {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            val body = JSONObject().put("event", eventName)
            if (!payload.isNullOrBlank()) body.put("payload", JSONObject(payload))
            if (itemId != null) body.put("itemId", itemId)
            if (actionClass != null) body.put("actionClass", actionClass)
            if (revision != null) body.put("revision", revision)
            if (clientEventId != null) body.put("clientEventId", clientEventId)
            if (!actionPayload.isNullOrBlank()) body.put("payload", JSONObject(actionPayload))
            if (confirmOnDevice) body.put("confirmOnDevice", true)
            conn.outputStream.write(body.toString().toByteArray(StandardCharsets.UTF_8))
            val code = conn.responseCode
            HttpResult(
                code = code,
                body = if (code in 200..299) conn.inputStream.bufferedReader().use { it.readText() } else null,
                retryAfterSeconds = retryAfter(conn),
            )
        } catch (e: Exception) {
            HttpResult(-1, error = e.message ?: e.javaClass.simpleName)
        } finally {
            conn.disconnect()
        }
    }

    fun reportInstances(
        baseUrl: String,
        widgetId: String,
        instances: List<Map<String, Any>>,
        token: String,
    ): HttpResult {
        val conn = open(
            baseUrl,
            "/v1/device/instances",
            token,
            method = "PUT",
        )
        return try {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            val body = JSONObject()
                .put("widgetId", widgetId)
                .put("instances", org.json.JSONArray(instances))
            conn.outputStream.write(body.toString().toByteArray(StandardCharsets.UTF_8))
            val code = conn.responseCode
            HttpResult(
                code = code,
                body = if (code in 200..299) conn.inputStream.bufferedReader().use { it.readText() } else null,
                retryAfterSeconds = retryAfter(conn),
            )
        } catch (e: Exception) {
            HttpResult(-1, error = e.message ?: e.javaClass.simpleName)
        } finally {
            conn.disconnect()
        }
    }

    fun clearPushEndpoint(baseUrl: String, token: String): HttpResult = registerPushEndpointInternal(
        baseUrl, token, null,
    )

    fun registerPushEndpoint(baseUrl: String, token: String, endpoint: String): HttpResult =
        registerPushEndpointInternal(baseUrl, token, endpoint)

    private fun registerPushEndpointInternal(baseUrl: String, token: String, endpoint: String?): HttpResult {
        val conn = open(baseUrl, "/v1/device", token, method = "PATCH")
        return try {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            val body = JSONObject()
            if (endpoint == null) body.put("pushEndpoint", JSONObject.NULL) else body.put("pushEndpoint", endpoint)
            conn.outputStream.write(body.toString().toByteArray(StandardCharsets.UTF_8))
            val code = conn.responseCode
            HttpResult(
                code = code,
                body = if (code in 200..299) conn.inputStream.bufferedReader().use { it.readText() } else null,
                retryAfterSeconds = retryAfter(conn),
            )
        } catch (e: Exception) {
            HttpResult(-1, error = e.message ?: e.javaClass.simpleName)
        } finally {
            conn.disconnect()
        }
    }

    private fun open(
        baseUrl: String,
        path: String,
        token: String,
        etag: String? = null,
        method: String = "GET",
    ): HttpURLConnection {
        val conn = URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 10_000
        conn.readTimeout = 20_000
        conn.setRequestProperty("Authorization", "Bearer $token")
        conn.setRequestProperty("Accept", "application/json, image/*")
        if (!etag.isNullOrEmpty()) conn.setRequestProperty("If-None-Match", etag)
        return conn
    }

    private fun pathSegment(value: String): String =
        URLEncoder.encode(value, StandardCharsets.UTF_8.name())

    private fun retryAfter(connection: HttpURLConnection): Int? =
        connection.getHeaderField("Retry-After")?.toIntOrNull()?.takeIf { it >= 0 }

    private fun readText(input: java.io.InputStream, limit: Int): String =
        String(readBytes(input, limit), StandardCharsets.UTF_8)

    private fun readBytes(input: java.io.InputStream, limit: Int): ByteArray {
        val declared = input.available().toLong()
        if (declared > limit) throw IllegalArgumentException("response exceeds size limit")
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(16 * 1024)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            if (output.size() + count > limit) {
                throw IllegalArgumentException("response exceeds size limit")
            }
            output.write(buffer, 0, count)
        }
        return output.toByteArray()
    }
}
