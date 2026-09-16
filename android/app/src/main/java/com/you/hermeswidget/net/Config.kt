package com.you.hermeswidget.net

import android.content.Context
import android.content.SharedPreferences

object Config {
    private const val PREFS_NAME = "hermes_config"
    private const val KEY_BACKEND_URL = "backend_url"
    private const val KEY_WIDGET_ID = "widget_id"
    private const val KEY_TOKEN = "token"
    private const val KEY_LAYOUT_JSON = "layout_json"

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
        return SecureStore.token(context) ?: getTokenFromPrefs(context)
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
}
