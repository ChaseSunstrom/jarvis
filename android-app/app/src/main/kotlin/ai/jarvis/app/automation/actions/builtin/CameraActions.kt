package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.ScanCodeActivity
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.PathScope
import ai.jarvis.app.automation.actions.granted
import ai.jarvis.app.automation.actions.intOr
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.markUntrusted
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.ui.ForegroundResultBridge
import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.ImageFormat
import android.hardware.camera2.CameraAccessException
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CameraMetadata
import android.hardware.camera2.CaptureFailure
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.ImageReader
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import android.view.WindowManager
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executor
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * The camera rows of the Tasker table (M61): a photo, and a barcode.
 *
 * `take_photo` is a headless Camera2 capture — no CameraX, no preview surface,
 * no dependency (`docs/TOOLING_DECISIONS.md`): open the camera, let the
 * exposure settle on a few small frames, take one JPEG, close. `scan_code`
 * bundles no decoder either; it hands off to the scanner app the settings
 * screen already uses and reports honestly when there is none.
 *
 * Everything that can be decided without a camera — which lens, which size,
 * which orientation, whether the exposure has settled, what a scanner's answer
 * means — is a pure function on the objects below, and
 * `CameraPhoneNfcActionsTest` proves those on the JVM.
 */

/** Tier 3 — a camera must never fire quietly, any more than a microphone. */
object TakePhoto : JarvisAction {
    override val id = "take_photo"
    override val tier = ActionTier.CONFIRM
    override val description = "Take one photo with the back (or front) camera into a JPEG under Jarvis's own files, without a preview. Asks first, every time. Read it back with read_file (base64)."
    override val paramsSchema = mapOf(
        "facing" to "string (optional): back (default) | front",
        "path" to "string (optional): the file to write under jarvis_files (default photos/<time>.jpg)",
        "max_edge" to "int $MIN_EDGE-$MAX_EDGE (optional): longest side in pixels (default $DEFAULT_EDGE)",
    )
    override val capability = "camera"
    override val requiredPermissions = listOf(Manifest.permission.CAMERA)
    override val timeoutMs: Long = 30_000L

    override fun isAvailable(ctx: Context): Boolean =
        ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)
    override val unsupportedReason: String get() = NO_CAMERA

    enum class Facing(val lensFacing: Int) {
        BACK(CameraCharacteristics.LENS_FACING_BACK),
        FRONT(CameraCharacteristics.LENS_FACING_FRONT),
    }

    /** Which way to look, or null for a word that is neither. */
    fun facingOf(raw: String?): Facing? = when (raw?.trim()?.lowercase().orEmpty().ifEmpty { "back" }) {
        "back", "rear" -> Facing.BACK
        "front", "selfie" -> Facing.FRONT
        else -> null
    }

    /** The first camera facing the wanted way, from (id, LENS_FACING) pairs; null when there is none. */
    fun chooseCamera(cameras: List<Pair<String, Int?>>, facing: Facing): String? =
        cameras.firstOrNull { (_, lens) -> lens == facing.lensFacing }?.first

    /**
     * The largest output no bigger than [maxEdge] on its long side, or the
     * smallest there is when even that is too big — a photo, never a refusal,
     * on a camera whose smallest JPEG is larger than asked for.
     */
    fun pickSize(sizes: List<Pair<Int, Int>>, maxEdge: Int): Pair<Int, Int>? {
        val fitting = sizes.filter { (w, h) -> maxOf(w, h) <= maxEdge }
        return fitting.maxByOrNull { (w, h) -> w.toLong() * h } ?: sizes.minByOrNull { (w, h) -> w.toLong() * h }
    }

    /** The longest side asked for, or null when it is present and outside the bounds. */
    fun maxEdgeOf(params: JSONObject): Int? {
        if (!params.has("max_edge")) return DEFAULT_EDGE
        return params.intOr("max_edge", -1).takeIf { it in MIN_EDGE..MAX_EDGE }
    }

    /**
     * The EXIF orientation to ask for, so the file is upright: the sensor's own
     * mounting plus how the phone is held (the front camera mirrors the turn).
     * Straight from the Camera2 reference; a phone held upright gives the
     * sensor orientation alone.
     */
    fun jpegOrientation(sensorOrientation: Int, facingFront: Boolean, deviceRotationDegrees: Int): Int {
        val rounded = (deviceRotationDegrees + 45) / 90 * 90
        val device = if (facingFront) -rounded else rounded
        return (sensorOrientation + device + 360) % 360
    }

    /**
     * Whether the 3A has done enough for a still: exposure converged (or the
     * scene simply needs the flash, which a headless capture will not use) and
     * focus at rest, or the frame budget spent — the picture is then taken as
     * it is rather than never.
     */
    fun settled(aeState: Int?, afState: Int?, frames: Int): Boolean {
        if (frames >= MAX_WARM_FRAMES) return true
        if (frames < MIN_WARM_FRAMES) return false
        val ae = aeState == null || aeState == CameraMetadata.CONTROL_AE_STATE_CONVERGED ||
            aeState == CameraMetadata.CONTROL_AE_STATE_FLASH_REQUIRED || aeState == CameraMetadata.CONTROL_AE_STATE_LOCKED
        val af = afState == null || afState == CameraMetadata.CONTROL_AF_STATE_INACTIVE ||
            afState == CameraMetadata.CONTROL_AF_STATE_PASSIVE_FOCUSED || afState == CameraMetadata.CONTROL_AF_STATE_PASSIVE_UNFOCUSED ||
            afState == CameraMetadata.CONTROL_AF_STATE_FOCUSED_LOCKED || afState == CameraMetadata.CONTROL_AF_STATE_NOT_FOCUSED_LOCKED
        return ae && af
    }

    /** Where the JPEG goes, inside the sandbox, or the reason it cannot. */
    fun pathOf(params: JSONObject, nowMs: Long): PathScope.Result =
        PathScope.normalize(params.str("path") ?: "photos/$nowMs.jpg")

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val facing = facingOf(params.str("facing")) ?: return ActionResult.error("facing must be back or front")
        val maxEdge = maxEdgeOf(params) ?: return ActionResult.error("max_edge must be between $MIN_EDGE and $MAX_EDGE")
        val relative = when (val r = pathOf(params, System.currentTimeMillis())) {
            is PathScope.Result.Rejected -> return ActionResult.error(r.reason)
            is PathScope.Result.Allowed -> r.relative
        }
        if (!ctx.granted(Manifest.permission.CAMERA)) return ActionResult.missingPermission(Manifest.permission.CAMERA)
        val cm = ctx.getSystemService(CameraManager::class.java) ?: return ActionResult.error("no camera service")

        val cameras = try {
            cm.cameraIdList.map { id -> id to cm.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) }
        } catch (e: CameraAccessException) {
            return ActionResult.error("the camera could not be listed: ${e.message ?: "reason ${e.reason}"}")
        }
        if (cameras.isEmpty()) return ActionResult.error(NO_CAMERA)
        val cameraId = chooseCamera(cameras, facing)
            ?: return ActionResult.error("this phone has no ${facing.name.lowercase()} camera")
        val characteristics = try {
            cm.getCameraCharacteristics(cameraId)
        } catch (e: CameraAccessException) {
            return ActionResult.error("the camera is not available: ${e.message ?: "reason ${e.reason}"}")
        }
        val map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            ?: return ActionResult.error("the camera reports no output sizes")
        val jpeg = pickSize(map.getOutputSizes(ImageFormat.JPEG).orEmpty().map { it.width to it.height }, maxEdge)
            ?: return ActionResult.error("the camera offers no JPEG output")
        val warm = pickSize(map.getOutputSizes(ImageFormat.YUV_420_888).orEmpty().map { it.width to it.height }, WARM_EDGE)
        val orientation = jpegOrientation(
            characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0,
            facing == Facing.FRONT,
            deviceRotationDegrees(ctx),
        )

        val bytes = try {
            Camera2Capture.captureJpeg(cm, cameraId, jpeg, warm, orientation)
        } catch (e: SecurityException) {
            return ActionResult.missingPermission(Manifest.permission.CAMERA)
        } catch (e: CameraAccessException) {
            return ActionResult.error("the camera is not available: ${e.message ?: "reason ${e.reason}"}")
        } catch (e: Camera2Capture.Failed) {
            return ActionResult.error(e.message ?: "the capture failed")
        }

        val file = File(File(ctx.filesDir, PathScope.ROOT_DIR_NAME), relative)
        file.parentFile?.mkdirs()
        return try {
            file.writeBytes(bytes)
            ActionResult.ok(
                json(
                    "path" to relative, "bytes" to bytes.size, "width" to jpeg.first, "height" to jpeg.second,
                    "facing" to facing.name.lowercase(), "camera_id" to cameraId,
                )
            )
        } catch (e: Exception) {
            ActionResult.error("could not write the photo: ${e.message ?: e.javaClass.simpleName}")
        }
    }

    /** How the phone is held, in degrees; 0 when there is no display to ask (a service context on 11+). */
    private fun deviceRotationDegrees(ctx: Context): Int {
        val rotation = runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                ctx.display?.rotation
            } else {
                @Suppress("DEPRECATION")
                ctx.getSystemService(WindowManager::class.java)?.defaultDisplay?.rotation
            }
        }.getOrNull() ?: Surface.ROTATION_0
        return when (rotation) {
            Surface.ROTATION_90 -> 90
            Surface.ROTATION_180 -> 180
            Surface.ROTATION_270 -> 270
            else -> 0
        }
    }

    const val NO_CAMERA = "this phone has no camera"
    const val MIN_EDGE = 320
    const val MAX_EDGE = 4096
    const val DEFAULT_EDGE = 1600
    /** The warm-up frames are small: the point is the 3A, not the pixels. */
    const val WARM_EDGE = 640
    const val MIN_WARM_FRAMES = 3
    const val MAX_WARM_FRAMES = 30
}

/**
 * A headless Camera2 still: open, configure, settle, capture, close — each
 * step under its own clock, so a camera that never answers is an error in
 * seconds rather than a hang until the dispatcher's timeout.
 */
internal object Camera2Capture {

    /** A capture-side failure with a sentence the model can act on. */
    class Failed(message: String) : Exception(message)

    private const val TAG = "JarvisCamera"
    private const val OPEN_MS = 5_000L
    private const val CONFIGURE_MS = 5_000L
    private const val WARM_MS = 3_000L
    private const val CAPTURE_MS = 8_000L

    suspend fun captureJpeg(
        cm: CameraManager,
        cameraId: String,
        jpeg: Pair<Int, Int>,
        warm: Pair<Int, Int>?,
        orientation: Int,
    ): ByteArray {
        val thread = HandlerThread("jarvis-camera").apply { start() }
        val handler = Handler(thread.looper)
        val executor = Executor { handler.post(it) }
        var device: CameraDevice? = null
        var session: CameraCaptureSession? = null
        var jpegReader: ImageReader? = null
        var warmReader: ImageReader? = null
        try {
            val opened = withTimeoutOrNull(OPEN_MS) { open(cm, cameraId, executor) }
                ?: throw Failed("the camera did not open within ${OPEN_MS / 1000} seconds")
            device = opened
            val stillReader = ImageReader.newInstance(jpeg.first, jpeg.second, ImageFormat.JPEG, 2)
            jpegReader = stillReader
            // A small YUV stream the 3A can converge on; every frame is dropped
            // on arrival. Warming up on the JPEG stream instead would have the
            // HAL encoding a full-size JPEG per frame for nothing.
            val warmup = warm?.let { (w, h) ->
                ImageReader.newInstance(w, h, ImageFormat.YUV_420_888, 3).also { reader ->
                    reader.setOnImageAvailableListener({ r -> r.acquireLatestImage()?.close() }, handler)
                }
            }
            warmReader = warmup
            val surfaces = listOfNotNull(warmup?.surface, stillReader.surface)
            val configured = withTimeoutOrNull(CONFIGURE_MS) { configure(opened, surfaces, executor) }
                ?: throw Failed("the camera did not configure within ${CONFIGURE_MS / 1000} seconds")
            session = configured
            if (warmup != null) {
                // Best effort: a HAL that never reports a state still gets a
                // photo after the frame budget, and one that reports nothing
                // at all gets one after the clock.
                withTimeoutOrNull(WARM_MS) { settle(configured, opened, warmup.surface, executor) }
                runCatching { configured.stopRepeating() }
            }
            return withTimeoutOrNull(CAPTURE_MS) { still(configured, opened, stillReader, orientation, executor, handler) }
                ?: throw Failed("the camera did not deliver a frame within ${CAPTURE_MS / 1000} seconds")
        } finally {
            runCatching { session?.close() }
            runCatching { device?.close() }
            runCatching { jpegReader?.close() }
            runCatching { warmReader?.close() }
            thread.quitSafely()
        }
    }

    private suspend fun open(cm: CameraManager, cameraId: String, executor: Executor): CameraDevice =
        suspendCancellableCoroutine { cont ->
            val callback = object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    if (cont.isActive) cont.resume(camera) else camera.close()
                }

                override fun onDisconnected(camera: CameraDevice) {
                    camera.close()
                    if (cont.isActive) cont.resumeWithException(Failed("the camera was disconnected"))
                }

                override fun onError(camera: CameraDevice, error: Int) {
                    camera.close()
                    if (cont.isActive) cont.resumeWithException(Failed(describe(error)))
                }
            }
            try {
                cm.openCamera(cameraId, executor, callback)
            } catch (e: SecurityException) {
                if (cont.isActive) cont.resumeWithException(e)
            } catch (e: CameraAccessException) {
                if (cont.isActive) cont.resumeWithException(e)
            }
        }

    /** The open errors, in words; the numbers are what logcat shows and nobody else reads. */
    fun describe(error: Int): String = when (error) {
        CameraDevice.StateCallback.ERROR_CAMERA_IN_USE -> "the camera is in use by another app"
        CameraDevice.StateCallback.ERROR_MAX_CAMERAS_IN_USE -> "too many cameras are open"
        CameraDevice.StateCallback.ERROR_CAMERA_DISABLED -> "the camera is disabled by device policy"
        CameraDevice.StateCallback.ERROR_CAMERA_DEVICE -> "the camera reported a hardware fault"
        CameraDevice.StateCallback.ERROR_CAMERA_SERVICE -> "the camera service failed"
        else -> "the camera failed to open (error $error)"
    }

    private suspend fun configure(device: CameraDevice, surfaces: List<Surface>, executor: Executor): CameraCaptureSession =
        suspendCancellableCoroutine { cont ->
            val callback = object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(session: CameraCaptureSession) {
                    if (cont.isActive) cont.resume(session) else session.close()
                }

                override fun onConfigureFailed(session: CameraCaptureSession) {
                    session.close()
                    if (cont.isActive) cont.resumeWithException(Failed("the camera refused this output configuration"))
                }
            }
            val config = SessionConfiguration(SessionConfiguration.SESSION_REGULAR, surfaces.map { OutputConfiguration(it) }, executor, callback)
            try {
                device.createCaptureSession(config)
            } catch (e: CameraAccessException) {
                if (cont.isActive) cont.resumeWithException(e)
            } catch (e: IllegalArgumentException) {
                if (cont.isActive) cont.resumeWithException(Failed("the camera refused this output configuration: ${e.message}"))
            }
        }

    private suspend fun settle(session: CameraCaptureSession, device: CameraDevice, surface: Surface, executor: Executor) =
        suspendCancellableCoroutine<Unit> { cont ->
            val request = device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                addTarget(surface)
                set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO)
                set(CaptureRequest.CONTROL_AF_MODE, CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                set(CaptureRequest.CONTROL_AE_MODE, CameraMetadata.CONTROL_AE_MODE_ON)
            }.build()
            var frames = 0
            val callback = object : CameraCaptureSession.CaptureCallback() {
                override fun onCaptureCompleted(s: CameraCaptureSession, r: CaptureRequest, result: TotalCaptureResult) {
                    frames++
                    val done = TakePhoto.settled(result.get(CaptureResult.CONTROL_AE_STATE), result.get(CaptureResult.CONTROL_AF_STATE), frames)
                    if (done && cont.isActive) cont.resume(Unit)
                }
            }
            try {
                session.setSingleRepeatingRequest(request, executor, callback)
            } catch (e: Exception) {
                // No warm-up is not no photo: the still is taken cold.
                Log.w(TAG, "warm-up stream refused; capturing cold", e)
                if (cont.isActive) cont.resume(Unit)
            }
        }

    private suspend fun still(
        session: CameraCaptureSession,
        device: CameraDevice,
        reader: ImageReader,
        orientation: Int,
        executor: Executor,
        handler: Handler,
    ): ByteArray = suspendCancellableCoroutine { cont ->
        reader.setOnImageAvailableListener({ r ->
            val image = r.acquireLatestImage() ?: return@setOnImageAvailableListener
            val bytes = try {
                val buffer = image.planes[0].buffer
                ByteArray(buffer.remaining()).also { buffer.get(it) }
            } finally {
                image.close()
            }
            if (cont.isActive) cont.resume(bytes)
        }, handler)
        val request = device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE).apply {
            addTarget(reader.surface)
            set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO)
            set(CaptureRequest.CONTROL_AF_MODE, CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
            set(CaptureRequest.CONTROL_AE_MODE, CameraMetadata.CONTROL_AE_MODE_ON)
            set(CaptureRequest.JPEG_ORIENTATION, orientation)
            set(CaptureRequest.JPEG_QUALITY, JPEG_QUALITY)
        }.build()
        val callback = object : CameraCaptureSession.CaptureCallback() {
            override fun onCaptureFailed(s: CameraCaptureSession, r: CaptureRequest, failure: CaptureFailure) {
                if (cont.isActive) cont.resumeWithException(Failed("the capture failed (reason ${failure.reason})"))
            }
        }
        try {
            session.captureSingleRequest(request, executor, callback)
        } catch (e: CameraAccessException) {
            if (cont.isActive) cont.resumeWithException(e)
        }
    }

    private const val JPEG_QUALITY: Byte = 90
}

/** Tier 1 — a scanner app opens in front of the person; nothing on the phone changes. */
object ScanCode : JarvisAction {
    override val id = "scan_code"
    override val tier = ActionTier.AUTO
    override val description = "Scan a QR code or barcode with the camera, through the phone's scanner app, and return the decoded text. Needs a scanner app that answers the ZXing SCAN intent (Binary Eye, QR Scanner)."
    override val paramsSchema = mapOf(
        "format" to "string (optional): any (default) | qr | product | 1d",
        "timeout_s" to "int $MIN_TIMEOUT_S-$MAX_TIMEOUT_S (optional): how long the scanner may stay open (default $DEFAULT_TIMEOUT_S)",
    )
    override val capability = "camera"

    /** A barcode's content is whoever printed it. */
    override val untrustedOutput = true
    override val timeoutMs: Long = (MAX_TIMEOUT_S + 15) * 1000L

    /** A scanner app must answer the intent; the camera itself is the scanner's, so no permission is asked for here. */
    override fun isAvailable(ctx: Context): Boolean =
        ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY) &&
            ctx.packageManager.resolveActivity(Intent(SCAN_ACTION), 0) != null
    override val unsupportedReason: String get() = NO_SCANNER

    /** The scanner's `SCAN_MODE` for a format word; null means any, and a word that is nothing is refused. */
    fun scanModeOf(raw: String?): Pair<String?, String?> = when (raw?.trim()?.lowercase().orEmpty().ifEmpty { "any" }) {
        "any", "all" -> null to null
        "qr", "qr_code", "qrcode" -> "QR_CODE_MODE" to null
        "product", "ean", "upc" -> "PRODUCT_MODE" to null
        "1d", "one_d", "barcode" -> "ONE_D_MODE" to null
        else -> null to "format must be any, qr, product or 1d"
    }

    /** Seconds the scanner may stay open, or null when the parameter is present and outside the bounds. */
    fun timeoutOf(params: JSONObject): Int? {
        if (!params.has("timeout_s")) return DEFAULT_TIMEOUT_S
        return params.intOr("timeout_s", -1).takeIf { it in MIN_TIMEOUT_S..MAX_TIMEOUT_S }
    }

    /** What the scanner's answer means: cancelled, empty, or a code — marked untrusted. */
    fun resultOf(cancelled: Boolean, text: String?, format: String?): ActionResult {
        if (cancelled) return ActionResult.error("the scan was cancelled")
        val code = text?.trim().orEmpty()
        if (code.isEmpty()) return ActionResult.error("nothing was scanned")
        return ActionResult.ok(json("text" to code, "format" to format?.trim()?.takeIf { it.isNotEmpty() }).markUntrusted())
    }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val (mode, error) = scanModeOf(params.str("format"))
        if (error != null) return ActionResult.error(error)
        val timeout = timeoutOf(params) ?: return ActionResult.error("timeout_s must be between $MIN_TIMEOUT_S and $MAX_TIMEOUT_S")
        if (ctx.packageManager.resolveActivity(Intent(SCAN_ACTION), 0) == null) return ActionResult.error(NO_SCANNER)
        val intent = Intent(ctx, ScanCodeActivity::class.java)
        mode?.let { intent.putExtra(ScanCodeActivity.EXTRA_SCAN_MODE, it) }
        return ForegroundResultBridge.run(ctx, intent, "the barcode scanner", timeout * 1000L)
    }

    const val SCAN_ACTION = "com.google.zxing.client.android.SCAN"
    const val NO_SCANNER = "no scanner app answers com.google.zxing.client.android.SCAN; install Binary Eye or QR Scanner (F-Droid)"
    const val MIN_TIMEOUT_S = 5
    const val MAX_TIMEOUT_S = 120
    const val DEFAULT_TIMEOUT_S = 60
}
