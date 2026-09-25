package com.you.hermeswidget.net

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

object SecureStore {
    private const val FILE = "hermes_secure"
    private const val KEY_TOKEN = "device_token"
    private const val KEY_BASE_URL = "backend_url"
    private const val KEY_DEVICE_ID = "device_id"

    private fun prefs(context: Context): SharedPreferences {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        return EncryptedSharedPreferences.create(
            context, FILE, masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }

    fun save(context: Context, baseUrl: String, token: String, deviceId: String?) {
        prefs(context).edit()
            .putString(KEY_BASE_URL, baseUrl)
            .putString(KEY_TOKEN, token)
            .putString(KEY_DEVICE_ID, deviceId)
            .apply()
    }

    fun token(context: Context): String? = prefs(context).getString(KEY_TOKEN, null)
    fun baseUrl(context: Context): String? = prefs(context).getString(KEY_BASE_URL, null)
    fun deviceId(context: Context): String? = prefs(context).getString(KEY_DEVICE_ID, null)
    fun clear(context: Context) = prefs(context).edit().clear().apply()
}
