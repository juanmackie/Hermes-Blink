package com.you.hermeswidget

import android.content.Context
import android.graphics.Color
import android.graphics.Matrix
import android.os.Bundle
import android.os.SystemClock
import android.text.method.ScrollingMovementMethod
import android.util.Log
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.AppCompatImageView
import androidx.core.widget.NestedScrollView
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.button.MaterialButton
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.TextInputEditText
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.HermesApi
import com.you.hermeswidget.net.Outcome
import com.you.hermeswidget.net.RequestUpdateEvent
import com.you.hermeswidget.net.PublicationAction
import com.you.hermeswidget.net.PublicationContent
import com.you.hermeswidget.net.PublicationRepository
import com.you.hermeswidget.net.SecureStore
import com.you.hermeswidget.work.DwellWorker
import com.you.hermeswidget.work.RefreshWorker
import com.you.hermeswidget.widget.PublicationImages
import org.json.JSONObject
import java.util.UUID
import kotlin.math.min

class PublicationActivity : AppCompatActivity() {
    private val openedAt = SystemClock.elapsedRealtime()
    private lateinit var root: View

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val publication = PublicationRepository.loadCached(this)
        if (publication == null || publication.isExpired()) {
            // This screen closes immediately, so there is no surface for a snackbar to
            // attach to and a message nobody can read is worse than none. The platform
            // toast is the one place it is still the right tool.
            Toast.makeText(this, "No current publication is available", Toast.LENGTH_LONG).show()
            finish()
            return
        }

        // The chrome is the layout; the body is whatever this publication contains.
        setContentView(R.layout.activity_publication)
        val toolbar = findViewById<MaterialToolbar>(R.id.top_app_bar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        // The title is the publication's own, in the app bar where MD3 puts a screen's
        // title, instead of a second 22sp heading in the body.
        toolbar.title = publication.title
        root = findViewById(R.id.publication_content)

        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            // The image content wants the space the rest of the body does not use, so the
            // column fills the viewport and the image takes the remainder by weight.
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
            setPadding(dp(20), dp(16), dp(20), dp(20))
        }
        (root as NestedScrollView).addView(content)

        content.addView(TextView(this).apply {
            // body-md: the summary is supporting text, not a second title.
            setTextAppearance(R.style.TextBodySmall)
            text = publication.summary
        })

        when (val body = publication.content) {
            is PublicationContent.Text -> content.addView(textView(body.text))
            is PublicationContent.Image -> {
                val bitmap = PublicationImages.load(this, publication)
                if (bitmap == null) {
                    content.addView(textView("The visual is not cached on this phone yet."))
                } else {
                    content.addView(
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
        publication.question?.takeIf { it.status == "open" }?.let { question ->
            content.addView(
                actionButton(R.layout.item_publication_action, getString(R.string.answer_question, question.prompt)) {
                    showQuestionDialog(question.questionId, question.prompt)
                },
            )
        }
        publication.ticker?.takeUnless { it.decayed }?.let { ticker ->
            val rotating = ticker.rotation.firstOrNull { !it.pinned }
            content.addView(TextView(this).apply {
                // body-md, the same step the summary uses: both are supporting text.
                setTextAppearance(R.style.TextBodySmall)
                text = getString(
                    R.string.ticker_line,
                    ticker.title,
                    rotating?.summary ?: ticker.summary,
                )
                setPadding(0, dp(12), 0, dp(4))
            })
        }
        // One primary action per screen. "Request update" is the one thing this screen is
        // for, so it is the only filled button; everything else steps down the MD3
        // hierarchy (tonal for the publication's own actions, text for inspection) rather
        // than a row of identical pills.
        content.addView(
            actionButton(R.layout.item_publication_primary_action, getString(R.string.widget_request_update)) {
                requestUpdate()
            },
        )
        content.addView(
            actionButton(R.layout.item_publication_text_action, getString(R.string.previous_states)) {
                showHistory()
            },
        )
        publication.actions.forEach { action ->
            val state = publication.actionStates[action.itemId]?.status
            val label = if (state == null || state == "queued") action.label else "${action.label} ($state)"
            content.addView(
                actionButton(R.layout.item_publication_action, label) {
                    confirmAndSend(action)
                }.apply {
                    isEnabled = state == null || state == "queued" || state == "awaiting_confirmation"
                },
            )
        }
        recordTapAndRefresh()
    }

    /**
     * Inflate one of the action item layouts and bind it. The appearance is the layout's
     * style; only the label and the click are decided here.
     */
    private fun actionButton(
        layout: Int,
        label: String,
        onClick: () -> Unit,
    ): MaterialButton =
        (layoutInflater.inflate(layout, null, false) as MaterialButton).apply {
            text = label
            setOnClickListener { onClick() }
        }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    /** MD3's transient message, in the app's own surface. */
    private fun say(message: String) {
        Snackbar.make(root, message, Snackbar.LENGTH_LONG).show()
    }

    /** Resolve a token, honouring the device's dark mode, with the caller's fallback. */
    private fun color(resId: Int, fallback: Int = 0): Int =
        runCatching { androidx.core.content.ContextCompat.getColor(this, resId) }
            .getOrDefault(if (fallback != 0) fallback else Color.GRAY)

    /**
     * Opening the zoom view is the only visible interaction affordance, so it
     * fetches now and reports the tap. Delivery states stay server-side; this
     * never claims the user read the content.
     */
    override fun onStop() {
        super.onStop()
        // Measured here, at the moment the view stopped being visible, and carried into the
        // worker: the report runs later and cannot re-derive it.
        val seconds = (SystemClock.elapsedRealtime() - openedAt) / 1000
        val bucket = when {
            seconds < 5 -> "lt5"
            seconds < 60 -> "5to60"
            else -> "gt60"
        }
        val publication = PublicationRepository.loadCached(this)
        if (publication == null) {
            reportDwellLost("no_cached_publication", "no cached publication to attribute the dwell to")
            return
        }
        val baseUrl = SecureStore.baseUrl(this) ?: Config.getBackendUrl(this)
        if (baseUrl == null) {
            reportDwellLost("no_server", "no widget server URL is configured")
            return
        }
        val token = SecureStore.token(this)
        if (token == null) {
            reportDwellLost("no_token", "no device token is stored")
            return
        }
        DwellWorker.enqueue(this, publication.widgetId, publication.revision, bucket)
    }

    /**
     * A dwell that cannot be reported says so. The three exits above used to be bare
     * `?: return`s, while the tap path beside them was made loud in round 5 — so a lost
     * measurement and a widget nobody opened stayed indistinguishable in the aggregate,
     * which is the one question dwell exists to answer.
     */
    private fun reportDwellLost(code: String, message: String) {
        Log.w("HermesDwell", "dwell not reported: $code - $message")
        Config.recordActionOutcome(
            this, "dwell", instanceId(), -1, code, message,
            source = Config.SOURCE_IN_APP_BUTTON,
        )
    }

    private fun requestUpdate() {
        // The three silent exits of round 5, made loud: a tap that cannot even be sent
        // now says which precondition is missing instead of returning quietly.
        val baseUrl = SecureStore.baseUrl(this) ?: Config.getBackendUrl(this)
        val token = SecureStore.token(this)
        val blocker = when {
            baseUrl == null -> Outcome.noServer()
            token == null -> Outcome.noToken()
            else -> null
        }
        if (blocker != null) {
            Log.w("HermesTap", "request_update not sent: ${blocker.code} — ${blocker.message}")
            Config.recordActionOutcome(
                this, "request_update", instanceId(), -1, blocker.code, blocker.message,
                source = Config.SOURCE_IN_APP_BUTTON,
            )
            say(blocker.message)
            return
        }
        val url = baseUrl!!
        val deviceToken = token!!
        Thread {
            val result = HermesApi.postEventWithFields(
                url, Config.getWidgetId(this), "request_update",
                // The same builder the widget path would use, with the other source: a
                // request_update from this screen is never the home-screen button.
                RequestUpdateEvent.inAppBody(instanceId()),
                deviceToken,
            )
            val resolved = Outcome.from(result, "Update requested", "Request update")
            val requestId = runCatching {
                result.body?.takeIf { it.isNotBlank() }?.let { JSONObject(it).optString("requestId") }
            }.getOrNull()
            // Logged as well as shown: a user who dismisses the message still leaves a
            // trail Diagnostics can display, with the server's own request id to quote.
            Log.i(
                "HermesTap",
                "request_update -> ${resolved.code} status=${result.code}" +
                    (requestId?.let { " requestId=$it" } ?: ""),
            )
            Config.recordActionOutcome(
                this, "request_update", instanceId(),
                resolved.httpStatus ?: result.code, resolved.code, resolved.message,
                source = Config.SOURCE_IN_APP_BUTTON,
            )
            runOnUiThread {
                say(resolved.message)
            }
        }.start()
    }

    private fun instanceId(): String? = intent?.getStringExtra(EXTRA_INSTANCE_ID)

    companion object {
        /** Lets a caller attribute a tap to a specific widget instance. */
        const val EXTRA_INSTANCE_ID = "com.you.hermeswidget.extra.INSTANCE_ID"
    }

    private fun showHistory() {
        val baseUrl = SecureStore.baseUrl(this) ?: Config.getBackendUrl(this) ?: return
        val token = SecureStore.token(this) ?: return
        Thread {
            val result = HermesApi.fetchHistory(baseUrl, Config.getWidgetId(this), token)
            runOnUiThread {
                if (result.code !in 200..299 || result.body == null) {
                    say("History unavailable")
                    return@runOnUiThread
                }
                val revisions = org.json.JSONObject(result.body).optJSONArray("revisions")
                val text = buildString {
                    for (index in 0 until (revisions?.length() ?: 0)) {
                        val item = revisions?.optJSONObject(index) ?: continue
                        append("r${item.optInt("revision")} · ${item.optString("title")}\n")
                    }
                }
                // MD3 alert dialog: 28dp corners from the shape scale, a tonal surface, and
                // text buttons rather than the platform's filled ones.
                MaterialAlertDialogBuilder(this)
                    .setTitle("Previous states")
                    .setMessage(text.ifBlank { "No history" })
                    .show()
            }
        }.start()
    }

    private fun showQuestionDialog(questionId: String, prompt: String) {
        // The answer is typed into a real MD3 text field, not a bare EditText: the label
        // sits in the outline, the focus cue is in primary, and the error slot exists if
        // the answer ever turns out to need one.
        val field = com.google.android.material.textfield.TextInputLayout(this).apply {
            setHint("Your answer")
            boxBackgroundMode =
                com.google.android.material.textfield.TextInputLayout.BOX_BACKGROUND_OUTLINE
            // The `medium` step of the shape scale, the same corner the fields on the
            // pairing screens use, so a dialog's field is not a different shape.
            val radius = dp(12).toFloat()
            setBoxCornerRadii(radius, radius, radius, radius)
        }
        val input = TextInputEditText(field.context).apply {
            maxLines = 3
        }
        field.addView(input)
        MaterialAlertDialogBuilder(this)
            .setTitle(prompt)
            .setView(field)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Send") { _, _ ->
                val baseUrl = SecureStore.baseUrl(this) ?: Config.getBackendUrl(this) ?: return@setPositiveButton
                val token = SecureStore.token(this) ?: return@setPositiveButton
                Thread {
                    HermesApi.postEventWithFields(
                        baseUrl, Config.getWidgetId(this), "answer",
                        org.json.JSONObject()
                            .put("questionId", questionId)
                            .put("answer", input.text.toString().trim()),
                        token,
                    )
                }.start()
            }
            .show()
    }

    private fun confirmAndSend(action: PublicationAction) {
        val sensitive = action.confirmOnDevice || action.actionClass in setOf("destructive", "external", "irreversible")
        if (!sensitive) {
            sendAction(action, confirmed = false)
            return
        }
        MaterialAlertDialogBuilder(this)
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
                say(
                    if (result.code in 200..299) "Action queued" else "Action saved; it will retry when connected",
                )
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
            // body-lg, the M3 step, applied as a role rather than a raw size: the old code
            // asked for 18sp, which is not on the type scale, and set the colour from a
            // token by hand where the role already carries it.
            setTextAppearance(R.style.TextBody)
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
