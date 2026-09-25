package com.you.hermeswidget

import android.app.Activity
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Matrix
import android.os.Bundle
import android.text.method.ScrollingMovementMethod
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import androidx.appcompat.widget.AppCompatImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.PublicationAction
import com.you.hermeswidget.net.PublicationContent
import com.you.hermeswidget.net.PublicationRepository
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.RefreshWorker
import com.you.hermeswidget.widget.PublicationImages
import org.json.JSONObject
import java.util.UUID
import kotlin.math.min

class PublicationActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val publication = PublicationRepository.loadCached(this)
        if (publication == null || publication.isExpired()) {
            Toast.makeText(this, "No current publication is available", Toast.LENGTH_LONG).show()
            finish()
            return
        }

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.rgb(20, 20, 20))
            setPadding(dp(20), dp(18), dp(20), dp(20))
        }
        root.addView(TextView(this).apply {
            text = publication.title
            setTextColor(Color.WHITE)
            textSize = 24f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        })
        root.addView(TextView(this).apply {
            text = publication.summary
            setTextColor(Color.rgb(205, 205, 205))
            textSize = 15f
            setPadding(0, dp(8), 0, dp(14))
        })

        when (val content = publication.content) {
            is PublicationContent.Text -> root.addView(textView(content.text))
            is PublicationContent.Image -> {
                val bitmap = PublicationImages.load(this, publication)
                if (bitmap == null) {
                    root.addView(textView("The visual is not cached on this phone yet."))
                } else {
                    root.addView(
                        ZoomImageView(this).apply {
                            setImageBitmap(bitmap)
                            contentDescription = publication.summary
                        },
                        LinearLayout.LayoutParams(
                            LinearLayout.LayoutParams.MATCH_PARENT,
                            0,
                            1f,
                        )
                    )
                }
            }
        }
        publication.actions.forEach { action ->
            val state = publication.actionStates[action.itemId]?.status
            val label = if (state == null || state == "queued") action.label else "${action.label} ($state)"
            root.addView(android.widget.Button(this).apply {
                text = label
                isEnabled = state == null || state == "queued" || state == "awaiting_confirmation"
                setOnClickListener { confirmAndSend(action) }
            })
        }
        setContentView(root)
        recordTapAndRefresh()
    }

    /**
     * Opening the zoom view is the only visible interaction affordance, so it
     * fetches now and reports the tap. Delivery states stay server-side; this
     * never claims the user read the content.
     */
    private fun confirmAndSend(action: PublicationAction) {
        val sensitive = action.confirmOnDevice || action.actionClass in setOf("destructive", "external", "irreversible")
        if (!sensitive) {
            sendAction(action, confirmed = false)
            return
        }
        AlertDialog.Builder(this)
            .setTitle("Queue this action?")
            .setMessage("This only queues an intent for the agent; it does not execute the operation here.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Queue") { _, _ -> sendAction(action, confirmed = true) }
            .show()
    }

    private fun sendAction(action: PublicationAction, confirmed: Boolean) {
        val baseUrl = SecureStore.baseUrl(this) ?: Config.getBackendUrl(this) ?: return
        val token = SecureStore.token(this) ?: return
        val publication = PublicationRepository.loadCached(this) ?: return
        val clientEventId = UUID.randomUUID().toString()
        Thread {
            val result = HermesApi.postAction(
                baseUrl, publication.widgetId, action.kind, action.itemId, action.actionClass,
                publication.revision, clientEventId, confirmed, token,
                JSONObject(action.payload).toString(),
            )
            if (result.code !in 200..299) {
                Config.enqueuePendingAction(this, JSONObject()
                    .put("event", action.kind)
                    .put("itemId", action.itemId)
                    .put("actionClass", action.actionClass)
                    .put("revision", publication.revision)
                    .put("clientEventId", clientEventId)
                    .put("confirmOnDevice", confirmed)
                    .put("payload", JSONObject(action.payload).toString()))
            }
            runOnUiThread {
                Toast.makeText(
                    this,
                    if (result.code in 200..299) "Action queued" else "Action saved; it will retry when connected",
                    Toast.LENGTH_LONG,
                ).show()
            }
        }.start()
    }

    private fun recordTapAndRefresh() {
        RefreshWorker.enqueueNow(this)
        val baseUrl = SecureStore.baseUrl(this) ?: Config.getBackendUrl(this) ?: return
        val token = SecureStore.token(this) ?: return
        Thread {
            runCatching {
                HermesApi.postEvent(baseUrl, Config.getWidgetId(this), "review", null, token)
            }
        }.start()
    }

    private fun textView(value: String): ScrollView = ScrollView(this).apply {
        addView(TextView(this@PublicationActivity).apply {
            text = value
            setTextColor(Color.WHITE)
            textSize = 18f
            setLineSpacing(0f, 1.2f)
            movementMethod = ScrollingMovementMethod()
            setTextIsSelectable(true)
        })
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()
}

private class ZoomImageView(context: Context) : AppCompatImageView(context) {
    private val transform = Matrix()
    private val scaleDetector = ScaleGestureDetector(context, ScaleListener())
    private var minimumScale = 1f
    private var maximumScale = 6f
    private var lastX = 0f
    private var lastY = 0f

    init {
        scaleType = ScaleType.MATRIX
        isClickable = true
    }

    override fun onSizeChanged(width: Int, height: Int, oldWidth: Int, oldHeight: Int) {
        super.onSizeChanged(width, height, oldWidth, oldHeight)
        val bitmap = drawable?.intrinsicWidth ?: 0
        val bitmapHeight = drawable?.intrinsicHeight ?: 0
        if (bitmap <= 0 || bitmapHeight <= 0 || width <= 0 || height <= 0) return
        val base = min(width.toFloat() / bitmap, height.toFloat() / bitmapHeight)
        minimumScale = base
        maximumScale = base * 6f
        transform.reset()
        transform.postScale(base, base)
        transform.postTranslate(
            (width - bitmap * base) / 2f,
            (height - bitmapHeight * base) / 2f,
        )
        imageMatrix = transform
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        scaleDetector.onTouchEvent(event)
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                lastX = event.x
                lastY = event.y
                parent.requestDisallowInterceptTouchEvent(true)
            }
            MotionEvent.ACTION_MOVE -> if (!scaleDetector.isInProgress && event.pointerCount == 1) {
                transform.postTranslate(event.x - lastX, event.y - lastY)
                imageMatrix = transform
                lastX = event.x
                lastY = event.y
            }
            MotionEvent.ACTION_UP -> {
                parent.requestDisallowInterceptTouchEvent(false)
                performClick()
            }
            MotionEvent.ACTION_CANCEL -> parent.requestDisallowInterceptTouchEvent(false)
        }
        return true
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }

    private fun currentScale(): Float {
        val values = FloatArray(9)
        transform.getValues(values)
        return values[Matrix.MSCALE_X]
    }

    private inner class ScaleListener : ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScale(detector: ScaleGestureDetector): Boolean {
            val current = currentScale().coerceAtLeast(minimumScale)
            val target = (current * detector.scaleFactor).coerceIn(minimumScale, maximumScale)
            val factor = if (current == 0f) 1f else target / current
            transform.postScale(factor, factor, detector.focusX, detector.focusY)
            imageMatrix = transform
            return true
        }
    }
}
