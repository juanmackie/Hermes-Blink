package com.you.hermeswidget.widget

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import com.caverock.androidsvg.SVG
import com.you.hermeswidget.net.Config
import com.you.hermeswidget.net.Publication
import com.you.hermeswidget.net.PublicationContent
import java.io.File
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

object PublicationImages {
    // ponytail: 2048px keeps widget/zoom decoding bounded; raise only if phone testing needs more detail.
    private const val MAX_RENDER_DIMENSION = 2048

    init {
        SVG.setInternalEntitiesEnabled(false)
    }

    fun load(context: android.content.Context, publication: Publication): Bitmap? {
        val image = publication.content as? PublicationContent.Image ?: return null
        val file = runCatching { Config.assetFile(context, image.assetId) }.getOrNull()
            ?.takeIf { it.isFile && it.length() == image.bytes }
            ?: return null
        return runCatching {
            if (image.mediaType == "image/svg+xml") decodeSvg(file) else decodeRaster(file)
        }.getOrNull()
    }

    private fun decodeRaster(file: File): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val options = BitmapFactory.Options().apply {
            inSampleSize = sampleSize(bounds.outWidth, bounds.outHeight, MAX_RENDER_DIMENSION)
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        return BitmapFactory.decodeFile(file.path, options)
    }

    private fun decodeSvg(file: File): Bitmap? {
        val svg = file.inputStream().use { SVG.getFromInputStream(it) }
        val viewBox = svg.documentViewBox
        val sourceWidth = svg.documentWidth
            .takeIf { it.isFinite() && it > 0f }
            ?.roundToInt()
            ?: viewBox?.width()?.roundToInt()
            ?: return null
        val sourceHeight = svg.documentHeight
            .takeIf { it.isFinite() && it > 0f }
            ?.roundToInt()
            ?: viewBox?.height()?.roundToInt()
            ?: return null
        val scale = min(1f, MAX_RENDER_DIMENSION.toFloat() / max(sourceWidth, sourceHeight))
        val width = max(1, (sourceWidth * scale).roundToInt())
        val height = max(1, (sourceHeight * scale).roundToInt())
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap).apply { drawColor(Color.WHITE) }
        svg.renderToPicture(width, height).draw(canvas)
        return bitmap
    }

    private fun sampleSize(width: Int, height: Int, limit: Int): Int {
        var sample = 1
        while (width / sample > limit || height / sample > limit) sample *= 2
        return sample
    }
}
