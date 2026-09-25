package com.you.hermeswidget.net

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

object Config {
    private const val PREFS_NAME = "hermes_config"
    private const val KEY_BACKEND_URL = "backend_url"
    private const val KEY_WIDGET_ID = "widget_id"
    private const val KEY_TOKEN = "token"
    private const val KEY_LAYOUT_JSON = "layout_json"
    private const val KEY_PUBLICATION_JSON = "publication_json"
    private const val KEY_PUBLICATION_ETAG = "publication_etag"
    private const val KEY_ASSET_ETAG = "asset_etag"
    private const val KEY_ASSET_ID = "asset_id"
    private const val KEY_CONNECTION_STATE = "connection_state"
    private const val KEY_LAST_CHECKED_AT = "last_checked_at"
    private const val KEY_LAST_POLL_AT = "last_poll_at"
    private const val KEY_LAST_FETCH_AT = "last_fetch_at"
    private const val KEY_LAST_RENDER_AT = "last_render_at"
    private const val KEY_BATTERY_EXEMPTION = "battery_exemption"
    private const val KEY_PENDING_ACTIONS = "pending_actions"

    private fun prefs(context: Context): SharedPreferences {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    fun setBackendUrl(context: Context, url: String) {
        prefs(context).edit().putString(KEY_BACKEND_URL, url.trim()).apply()
    }

    fun getBackendUrl(context: Context): String? {
        val url = prefs(context).getString(KEY_BACKEND_URL, null)
        return url?.takeIf { it.isNotEmpty() }
    }

    fun setToken(context: Context, token: String?) {
        // Delegate to encrypted storage; never keep a plaintext copy in
        // ordinary SharedPreferences. See review finding 4 / Config.kt.
        val currentUrl = SecureStore.baseUrl(context) ?: getBackendUrl(context) ?: ""
        val currentDeviceId = SecureStore.deviceId(context) ?: ""
        if (token == null || token.isEmpty()) {
            SecureStore.clear(context)
            // Preserve URL in ordinary prefs for quick access
            setBackendUrl(context, currentUrl)
        } else {
            SecureStore.save(context, currentUrl, token, currentDeviceId)
        }
        // Always remove any leftover plaintext token copy from ordinary prefs.
        prefs(context).edit().remove(KEY_TOKEN).apply()
    }

    fun getToken(context: Context): String? {
        SecureStore.token(context)?.let { return it }
        // One-time migration: plaintext -> encrypted, then remove leftover
        val plaintext = getTokenFromPrefs(context)
        if (plaintext != null) {
            val url = getBackendUrl(context) ?: SecureStore.baseUrl(context) ?: ""
            val deviceId = SecureStore.deviceId(context) ?: ""
            SecureStore.save(context, url, plaintext, deviceId)
            prefs(context).edit().remove(KEY_TOKEN).apply()
            return plaintext
        }
        return null
    }

    private fun getTokenFromPrefs(context: Context): String? {
        val token = prefs(context).getString(KEY_TOKEN, "")
        return token?.takeIf { it.isNotEmpty() }
    }

    fun setWidgetId(context: Context, id: String) {
        prefs(context).edit().putString(KEY_WIDGET_ID, id).apply()
    }

    fun getWidgetId(context: Context): String {
        return prefs(context).getString(KEY_WIDGET_ID, "hermes-brief") ?: "hermes-brief"
    }

    fun setCachedLayout(context: Context, json: String) {
        prefs(context).edit().putString(KEY_LAYOUT_JSON, json).apply()
    }

    fun getCachedLayout(context: Context): String? {
        val json = prefs(context).getString(KEY_LAYOUT_JSON, null)
        return json?.takeIf { it.isNotEmpty() }
    }

    fun setCachedPublication(
        context: Context,
        json: String,
        publicationEtag: String?,
        assetId: String?,
        assetEtag: String?,
    ): Boolean {
        val editor = prefs(context).edit()
            .putString(KEY_PUBLICATION_JSON, json)
            .putString(KEY_PUBLICATION_ETAG, publicationEtag)
            .putString(KEY_ASSET_ID, assetId)
            .putString(KEY_ASSET_ETAG, assetEtag)
        return editor.commit()
    }

    fun getCachedPublication(context: Context): String? {
        return prefs(context).getString(KEY_PUBLICATION_JSON, null)
            ?.takeIf { it.isNotEmpty() }
    }

    fun getPublicationEtag(context: Context): String? {
        return prefs(context).getString(KEY_PUBLICATION_ETAG, null)
            ?.takeIf { it.isNotEmpty() }
    }

    fun getAssetEtag(context: Context, assetId: String): String? {
        if (prefs(context).getString(KEY_ASSET_ID, null) != assetId) return null
        return prefs(context).getString(KEY_ASSET_ETAG, null)?.takeIf { it.isNotEmpty() }
    }

    fun clearCachedPublication(context: Context): Boolean {
        return prefs(context).edit()
            .remove(KEY_PUBLICATION_JSON)
            .remove(KEY_PUBLICATION_ETAG)
            .remove(KEY_ASSET_ID)
            .remove(KEY_ASSET_ETAG)
            .commit()
    }

    fun assetFile(context: Context, assetId: String): File {
        require(ASSET_ID_PATTERN.matches(assetId)) { "invalid asset id" }
        return File(File(context.filesDir, "publication-assets"), assetId)
    }

    fun setConnectionState(context: Context, state: ConnectionState, checkedAt: Long) {
        prefs(context).edit()
            .putString(KEY_CONNECTION_STATE, state.name)
            .putLong(KEY_LAST_CHECKED_AT, checkedAt)
            .apply()
    }

    fun getConnectionState(context: Context): ConnectionState {
        val stored = prefs(context).getString(KEY_CONNECTION_STATE, null)
        return stored?.let { runCatching { ConnectionState.valueOf(it) }.getOrNull() }
            ?: if (SecureStore.token(context).isNullOrEmpty()) ConnectionState.UNPAIRED else ConnectionState.OFFLINE
    }

    fun getLastCheckedAt(context: Context): Long? {
        return prefs(context).getLong(KEY_LAST_CHECKED_AT, 0L).takeIf { it > 0L }
    }

    fun setDiagnosticTime(context: Context, key: String, at: Long = System.currentTimeMillis()) {
        require(key in setOf("poll", "fetch", "render")) { "unknown diagnostic time" }
        prefs(context).edit().putLong(
            when (key) {
                "poll" -> KEY_LAST_POLL_AT
                "fetch" -> KEY_LAST_FETCH_AT
                else -> KEY_LAST_RENDER_AT
            }, at
        ).apply()
    }

    fun getDiagnosticTimes(context: Context): Map<String, Long?> = mapOf(
        "lastPollAt" to prefs(context).getLong(KEY_LAST_POLL_AT, 0L).takeIf { it > 0L },
        "lastFetchAt" to prefs(context).getLong(KEY_LAST_FETCH_AT, 0L).takeIf { it > 0L },
        "lastRenderAt" to prefs(context).getLong(KEY_LAST_RENDER_AT, 0L).takeIf { it > 0L },
    )

    fun setBatteryExemptionHint(context: Context, granted: Boolean) {
        prefs(context).edit().putBoolean(KEY_BATTERY_EXEMPTION, granted).apply()
    }

    fun getBatteryExemptionHint(context: Context): Boolean =
        prefs(context).getBoolean(KEY_BATTERY_EXEMPTION, false)

    /** A small bounded outbox for taps made while the private path is unavailable. */
    fun enqueuePendingAction(context: Context, action: JSONObject) {
        val id = action.optString("clientEventId")
        if (id.isBlank()) return
        val current = runCatching {
            JSONArray(prefs(context).getString(KEY_PENDING_ACTIONS, "[]") ?: "[]")
        }.getOrElse { JSONArray() }
        val next = JSONArray()
        for (index in 0 until current.length()) {
            val item = current.optJSONObject(index) ?: continue
            if (item.optString("clientEventId") != id) next.put(item)
        }
        next.put(action)
        while (next.length() > 50) next.remove(0)
        prefs(context).edit().putString(KEY_PENDING_ACTIONS, next.toString()).apply()
    }

    fun pendingActions(context: Context): List<JSONObject> {
        val array = runCatching {
            JSONArray(prefs(context).getString(KEY_PENDING_ACTIONS, "[]") ?: "[]")
        }.getOrElse { JSONArray() }
        return (0 until array.length()).mapNotNull { array.optJSONObject(it) }
    }

    fun removePendingAction(context: Context, clientEventId: String) {
        val current = runCatching {
            JSONArray(prefs(context).getString(KEY_PENDING_ACTIONS, "[]") ?: "[]")
        }.getOrElse { JSONArray() }
        val next = JSONArray()
        for (index in 0 until current.length()) {
            val item = current.optJSONObject(index) ?: continue
            if (item.optString("clientEventId") != clientEventId) next.put(item)
        }
        prefs(context).edit().putString(KEY_PENDING_ACTIONS, next.toString()).apply()
    }
}
