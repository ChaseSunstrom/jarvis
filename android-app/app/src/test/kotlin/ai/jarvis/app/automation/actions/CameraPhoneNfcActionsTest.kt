package ai.jarvis.app.automation.actions

import ai.jarvis.app.automation.actions.builtin.Builtins
import ai.jarvis.app.automation.actions.builtin.EndCall
import ai.jarvis.app.automation.actions.builtin.NdefCodec
import ai.jarvis.app.automation.actions.builtin.NfcRead
import ai.jarvis.app.automation.actions.builtin.NfcWrite
import ai.jarvis.app.automation.actions.builtin.ReadCallLog
import ai.jarvis.app.automation.actions.builtin.ReadSms
import ai.jarvis.app.automation.actions.builtin.ScanCode
import ai.jarvis.app.automation.actions.builtin.TakePhoto
import ai.jarvis.app.automation.policy.ActionTier
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraMetadata
import android.provider.CallLog
import android.provider.Telephony
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The last six Tasker rows (M61 — `docs/ANDROID_TASKER_PARITY.md`): the ones
 * that needed a camera, the SMS and call-log providers, hanging up, and NFC.
 * Proved here is everything a JVM can prove — which lens, which size, which
 * orientation, when the exposure has settled, what a filter selects, what a
 * scanner's or a tag's bytes mean, what is refused, and that every result
 * that carries somebody else's words says so. What a handset does with them
 * is `docs/ANDROID_DEVICE_TESTS.md` ADT-040 onwards.
 */
class CameraPhoneNfcActionsTest {

    // --- registration and tiers ----------------------------------------------

    @Test
    fun `the seven ids are registered once, at the tier the parity table states`() {
        val all = Builtins.all()
        for (id in listOf("take_photo", "scan_code", "read_sms", "read_call_log", "end_call", "nfc_read", "nfc_write")) {
            assertEquals("$id registered once", 1, all.count { it.id == id })
        }
        // A camera and a microphone are the two things that must never fire quietly.
        assertEquals(ActionTier.CONFIRM, TakePhoto.tier)
        // The scanner app opens in front of the person; nothing on the phone changes.
        assertEquals(ActionTier.AUTO, ScanCode.tier)
        // Other people's words and numbers: asked about every time.
        assertEquals(ActionTier.CONFIRM, ReadSms.tier)
        assertEquals(ActionTier.CONFIRM, ReadCallLog.tier)
        // Hanging up is done to a person, like dialling one.
        assertEquals(ActionTier.CONFIRM, EndCall.tier)
        // A tag has to be held to the phone by hand; asking once is enough.
        assertEquals(ActionTier.NOTIFY, NfcRead.tier)
        assertEquals(ActionTier.NOTIFY, NfcWrite.tier)
    }

    @Test
    fun `every result that carries somebody else's text is declared untrusted`() {
        assertTrue(ReadSms.untrustedOutput)
        assertTrue(ReadCallLog.untrustedOutput)
        assertTrue(ScanCode.untrustedOutput)
        assertTrue(NfcRead.untrustedOutput)
        // ...and the ones that return nothing anyone else wrote are not.
        assertFalse(TakePhoto.untrustedOutput)
        assertFalse(EndCall.untrustedOutput)
        assertFalse(NfcWrite.untrustedOutput)
    }

    @Test
    fun `each names the permission the dispatcher must ask for`() {
        assertEquals(listOf("android.permission.CAMERA"), TakePhoto.requiredPermissions)
        assertEquals(listOf("android.permission.READ_SMS"), ReadSms.requiredPermissions)
        assertEquals(listOf("android.permission.READ_CALL_LOG"), ReadCallLog.requiredPermissions)
        assertEquals(listOf("android.permission.ANSWER_PHONE_CALLS"), EndCall.requiredPermissions)
        // The scanner app holds the camera; NFC is a normal permission. Asking
        // for either here would raise a dialog for a grant nothing needs.
        assertTrue(ScanCode.requiredPermissions.isEmpty())
        assertTrue(NfcRead.requiredPermissions.isEmpty())
        assertTrue(NfcWrite.requiredPermissions.isEmpty())
        assertEquals("permission android.permission.CAMERA not granted", ActionResult.missingPermission("android.permission.CAMERA").error)
    }

    // --- take_photo ------------------------------------------------------------

    @Test
    fun `take_photo looks back unless asked to look front, and refuses a third way`() {
        assertEquals(TakePhoto.Facing.BACK, TakePhoto.facingOf(null))
        assertEquals(TakePhoto.Facing.BACK, TakePhoto.facingOf(" Rear "))
        assertEquals(TakePhoto.Facing.FRONT, TakePhoto.facingOf("selfie"))
        assertNull(TakePhoto.facingOf("sideways"))
    }

    @Test
    fun `take_photo picks the first lens facing the right way and says when there is none`() {
        val cameras = listOf(
            "0" to CameraCharacteristics.LENS_FACING_BACK,
            "1" to CameraCharacteristics.LENS_FACING_FRONT,
            "2" to CameraCharacteristics.LENS_FACING_BACK,
        )
        assertEquals("0", TakePhoto.chooseCamera(cameras, TakePhoto.Facing.BACK))
        assertEquals("1", TakePhoto.chooseCamera(cameras, TakePhoto.Facing.FRONT))
        assertNull(TakePhoto.chooseCamera(listOf("0" to CameraCharacteristics.LENS_FACING_BACK), TakePhoto.Facing.FRONT))
        assertNull(TakePhoto.chooseCamera(emptyList(), TakePhoto.Facing.BACK))
        assertEquals("this phone has no camera", TakePhoto.NO_CAMERA)
    }

    @Test
    fun `take_photo takes the largest size that fits, or the smallest when none does`() {
        val sizes = listOf(4032 to 3024, 1920 to 1080, 1600 to 1200, 640 to 480)
        assertEquals(1600 to 1200, TakePhoto.pickSize(sizes, 1600))
        assertEquals(4032 to 3024, TakePhoto.pickSize(sizes, 4096))
        // A camera whose smallest JPEG is bigger than asked for still takes a photo.
        assertEquals(640 to 480, TakePhoto.pickSize(sizes, 320))
        assertNull(TakePhoto.pickSize(emptyList(), 1600))
        assertEquals(TakePhoto.DEFAULT_EDGE, TakePhoto.maxEdgeOf(JSONObject()))
        assertNull(TakePhoto.maxEdgeOf(JSONObject().put("max_edge", 10)))
        assertNull(TakePhoto.maxEdgeOf(JSONObject().put("max_edge", 100000)))
        assertEquals(1024, TakePhoto.maxEdgeOf(JSONObject().put("max_edge", 1024)))
    }

    @Test
    fun `take_photo asks for an upright jpeg from the sensor mounting and how the phone is held`() {
        // A phone held upright: the sensor's own mounting, which is 90 on nearly every handset.
        assertEquals(90, TakePhoto.jpegOrientation(90, facingFront = false, deviceRotationDegrees = 0))
        // Turned to landscape: the back camera adds the turn, the front camera subtracts it.
        assertEquals(180, TakePhoto.jpegOrientation(90, facingFront = false, deviceRotationDegrees = 90))
        assertEquals(180, TakePhoto.jpegOrientation(270, facingFront = true, deviceRotationDegrees = 90))
        assertEquals(0, TakePhoto.jpegOrientation(90, facingFront = false, deviceRotationDegrees = 270))
        // Sixty degrees is rounded to the nearest quarter turn, as the reference does.
        assertEquals(180, TakePhoto.jpegOrientation(90, facingFront = false, deviceRotationDegrees = 60))
    }

    @Test
    fun `take_photo waits for the exposure to settle, and not forever`() {
        val converged = CameraMetadata.CONTROL_AE_STATE_CONVERGED
        val focused = CameraMetadata.CONTROL_AF_STATE_PASSIVE_FOCUSED
        // Too early, whatever the states say: the first frames are garbage.
        assertFalse(TakePhoto.settled(converged, focused, frames = 1))
        assertTrue(TakePhoto.settled(converged, focused, frames = TakePhoto.MIN_WARM_FRAMES))
        // Still searching: keep waiting.
        assertFalse(TakePhoto.settled(CameraMetadata.CONTROL_AE_STATE_SEARCHING, focused, frames = 10))
        assertFalse(TakePhoto.settled(converged, CameraMetadata.CONTROL_AF_STATE_PASSIVE_SCAN, frames = 10))
        // A dark room says "flash required"; a headless capture has no flash and takes the frame.
        assertTrue(TakePhoto.settled(CameraMetadata.CONTROL_AE_STATE_FLASH_REQUIRED, focused, frames = 5))
        // A HAL that reports nothing is not a HAL that gets to hang the capture.
        assertTrue(TakePhoto.settled(null, null, frames = TakePhoto.MIN_WARM_FRAMES))
        assertTrue(TakePhoto.settled(CameraMetadata.CONTROL_AE_STATE_SEARCHING, null, frames = TakePhoto.MAX_WARM_FRAMES))
    }

    @Test
    fun `take_photo writes inside the sandbox and nowhere else`() {
        assertEquals(PathScope.Result.Allowed("photos/1700000000000.jpg"), TakePhoto.pathOf(JSONObject(), 1700000000000L))
        assertEquals(PathScope.Result.Allowed("shots/door.jpg"), TakePhoto.pathOf(JSONObject().put("path", "shots/door.jpg"), 0L))
        assertTrue(TakePhoto.pathOf(JSONObject().put("path", "../../DCIM/x.jpg"), 0L) is PathScope.Result.Rejected)
        assertTrue(TakePhoto.pathOf(JSONObject().put("path", "/sdcard/x.jpg"), 0L) is PathScope.Result.Rejected)
    }

    // --- scan_code -------------------------------------------------------------

    @Test
    fun `scan_code maps a format word to the scanner's mode and refuses a word that is none`() {
        assertEquals(null to null, ScanCode.scanModeOf(null))
        assertEquals(null to null, ScanCode.scanModeOf("any"))
        assertEquals("QR_CODE_MODE" to null, ScanCode.scanModeOf(" QR "))
        assertEquals("PRODUCT_MODE" to null, ScanCode.scanModeOf("ean"))
        assertEquals("ONE_D_MODE" to null, ScanCode.scanModeOf("barcode"))
        assertTrue(ScanCode.scanModeOf("hologram").second!!.contains("format must be"))
        assertEquals(ScanCode.DEFAULT_TIMEOUT_S, ScanCode.timeoutOf(JSONObject()))
        assertNull(ScanCode.timeoutOf(JSONObject().put("timeout_s", 1)))
        assertNull(ScanCode.timeoutOf(JSONObject().put("timeout_s", 999)))
        assertEquals(20, ScanCode.timeoutOf(JSONObject().put("timeout_s", 20)))
    }

    @Test
    fun `scan_code reports a cancelled or empty scan as an error and a code as untrusted text`() {
        assertEquals("the scan was cancelled", ScanCode.resultOf(cancelled = true, text = "x", format = "QR_CODE").error)
        assertEquals("nothing was scanned", ScanCode.resultOf(cancelled = false, text = "  ", format = null).error)
        val ok = ScanCode.resultOf(cancelled = false, text = " https://example.invalid/t ", format = "QR_CODE")
        assertTrue(ok.ok)
        assertEquals("https://example.invalid/t", ok.data!!.getString("text"))
        assertEquals("QR_CODE", ok.data!!.getString("format"))
        // A barcode's content is whoever printed it.
        assertTrue(ok.data!!.getBoolean("untrusted"))
        assertTrue(ScanCode.NO_SCANNER.contains("install"))
        assertEquals("com.google.zxing.client.android.SCAN", ScanCode.SCAN_ACTION)
    }

    // --- read_sms / read_call_log ---------------------------------------------

    @Test
    fun `read_sms selects the box, the sender and the time it was asked for`() {
        assertEquals(Telephony.Sms.MESSAGE_TYPE_INBOX to null, ReadSms.boxOf(null))
        assertEquals(Telephony.Sms.MESSAGE_TYPE_SENT to null, ReadSms.boxOf("Sent"))
        assertEquals(null to null, ReadSms.boxOf("all"))
        assertTrue(ReadSms.boxOf("spam").second!!.contains("box must be"))
        assertEquals(null to null, ReadSms.selectionOf(null, null, null))
        val (where, args) = ReadSms.selectionOf(Telephony.Sms.MESSAGE_TYPE_INBOX, "7700", 1_700_000_000_000L)
        assertEquals("type = ? AND address LIKE ? AND date >= ?", where)
        assertEquals(listOf("1", "%7700%", "1700000000000"), args!!.toList())
        // A blank sender is no filter, not a filter on nothing.
        assertEquals("type = ?", ReadSms.selectionOf(Telephony.Sms.MESSAGE_TYPE_INBOX, "  ", null).first)
        assertEquals("received", ReadSms.directionOf(Telephony.Sms.MESSAGE_TYPE_INBOX))
        assertEquals("sent", ReadSms.directionOf(Telephony.Sms.MESSAGE_TYPE_SENT))
        assertEquals("other", ReadSms.directionOf(99))
    }

    @Test
    fun `read_call_log knows every kind of call and selects by it`() {
        assertEquals(null to null, ReadCallLog.typeOf(null))
        assertEquals(CallLog.Calls.MISSED_TYPE to null, ReadCallLog.typeOf(" Missed "))
        assertEquals(CallLog.Calls.REJECTED_TYPE to null, ReadCallLog.typeOf("declined"))
        assertTrue(ReadCallLog.typeOf("prank").second!!.contains("type must be"))
        val (where, args) = ReadCallLog.selectionOf(CallLog.Calls.MISSED_TYPE, 1_700_000_000_000L)
        assertEquals("type = ? AND date >= ?", where)
        assertEquals(listOf("3", "1700000000000"), args!!.toList())
        assertEquals(null to null, ReadCallLog.selectionOf(null, null))
        for (type in listOf(CallLog.Calls.INCOMING_TYPE, CallLog.Calls.OUTGOING_TYPE, CallLog.Calls.MISSED_TYPE, CallLog.Calls.REJECTED_TYPE, CallLog.Calls.BLOCKED_TYPE, CallLog.Calls.VOICEMAIL_TYPE)) {
            assertTrue("type $type has a word", ReadCallLog.nameOfType(type) != "other")
        }
        assertEquals("other", ReadCallLog.nameOfType(42))
    }

    @Test
    fun `the log reads take a handful by default and never the whole provider`() {
        assertEquals(10, ai.jarvis.app.automation.actions.builtin.LogLimit.of(JSONObject()))
        assertEquals(1, ai.jarvis.app.automation.actions.builtin.LogLimit.of(JSONObject().put("limit", -5)))
        assertEquals(50, ai.jarvis.app.automation.actions.builtin.LogLimit.of(JSONObject().put("limit", 5000)))
    }

    // --- end_call ------------------------------------------------------------

    @Test
    fun `end_call says when there was nothing to hang up`() {
        assertTrue(EndCall.resultOf(true).ok)
        assertTrue(EndCall.resultOf(true).data!!.getBoolean("ended"))
        val nothing = EndCall.resultOf(false)
        assertFalse(nothing.ok)
        assertEquals(EndCall.NO_CALL, nothing.error)
        assertTrue(EndCall.paramsSchema.isEmpty())
    }

    // --- nfc_read / nfc_write ------------------------------------------------

    @Test
    fun `ndef text records round-trip byte for byte, in the spec's layout`() {
        val payload = NdefCodec.encodeText("Hello, tag", "en")
        // status byte = language length (UTF-8 flag clear), then "en", then the text
        assertEquals(2, payload[0].toInt())
        assertEquals("en", NdefCodec.languageOf(payload))
        assertEquals("Hello, tag", NdefCodec.decodeText(payload))
        assertEquals("Grüße", NdefCodec.decodeText(NdefCodec.encodeText("Grüße")))
        // A UTF-16 record from another writer is read as one.
        val utf16 = byteArrayOf((0x80 or 2).toByte()) + "en".toByteArray() + "Hi".toByteArray(Charsets.UTF_16)
        assertEquals("Hi", NdefCodec.decodeText(utf16))
        assertNull(NdefCodec.decodeText(ByteArray(0)))
        assertNull(NdefCodec.decodeText(byteArrayOf(9, 'e'.code.toByte())))
    }

    @Test
    fun `ndef uri records abbreviate the longest known prefix and decode every code`() {
        val https = NdefCodec.encodeUri("https://www.example.invalid/x")
        assertEquals(0x02, https[0].toInt())
        assertEquals("https://www.example.invalid/x", NdefCodec.decodeUri(https))
        assertEquals(0x05, NdefCodec.encodeUri("tel:+447700900123")[0].toInt())
        assertEquals(0x00, NdefCodec.encodeUri("jarvis://home")[0].toInt())
        assertEquals("jarvis://home", NdefCodec.decodeUri(NdefCodec.encodeUri("jarvis://home")))
        assertEquals(36, NdefCodec.URI_PREFIXES.size)
        assertNull(NdefCodec.decodeUri(byteArrayOf(0x40, 'x'.code.toByte())))
        assertNull(NdefCodec.decodeUri(ByteArray(0)))
    }

    @Test
    fun `nfc_read classifies records and marks the whole tag untrusted`() {
        val text = NfcRead.Record.classify(1, NfcRead.Record.RTD_TEXT, NdefCodec.encodeText("gate code 4471"))
        assertEquals("text", text.type)
        assertEquals("gate code 4471", text.text)
        val uri = NfcRead.Record.classify(1, NfcRead.Record.RTD_URI, NdefCodec.encodeUri("https://example.invalid"))
        assertEquals("https://example.invalid", uri.uri)
        val mime = NfcRead.Record.classify(2, "text/plain".toByteArray(), "plain".toByteArray())
        assertEquals("text/plain", mime.mime)
        assertEquals("plain", mime.text)
        assertNull(NfcRead.Record.classify(2, "application/octet-stream".toByteArray(), byteArrayOf(0, 1, 2)).text)
        assertEquals("empty", NfcRead.Record.classify(0, ByteArray(0), ByteArray(0)).type)
        val tag = NfcRead.describe(byteArrayOf(0x04, 0xA1.toByte(), 0x2F), arrayOf("android.nfc.tech.NfcA", "android.nfc.tech.Ndef"), listOf(text, uri), ndef = true)
        assertEquals("04a12f", tag.getString("tag_id"))
        assertEquals("NfcA", tag.getJSONArray("tech").getString(0))
        assertEquals("gate code 4471", tag.getString("text"))
        assertEquals("https://example.invalid", tag.getString("uri"))
        assertEquals(2, tag.getJSONArray("records").length())
        assertTrue(tag.getBoolean("untrusted"))
        // A tag with no NDEF is still a tag: its id is the useful part.
        val bare = NfcRead.describe(byteArrayOf(0x08), arrayOf("android.nfc.tech.MifareClassic"), emptyList(), ndef = false)
        assertFalse(bare.getBoolean("ndef"))
        assertFalse(bare.has("text"))
        assertTrue(NfcRead.NFC_OFF.contains("panel nfc"))
    }

    @Test
    fun `nfc_write takes one text or one uri and bounds the wait`() {
        assertEquals("text or uri is required", NfcWrite.payloadOf(JSONObject()).second)
        assertEquals("write either text or uri, not both", NfcWrite.payloadOf(JSONObject().put("text", "a").put("uri", "tel:1")).second)
        assertTrue(NfcWrite.payloadOf(JSONObject().put("text", "x".repeat(NfcWrite.MAX_CHARS + 1))).second!!.contains("too long"))
        assertTrue(NfcWrite.payloadOf(JSONObject().put("uri", "example.invalid")).second!!.contains("scheme"))
        assertTrue(NfcWrite.payloadOf(JSONObject().put("uri", "https://example.invalid/a b")).second!!.contains("spaces"))
        assertEquals(NfcWrite.Payload("hello", null), NfcWrite.payloadOf(JSONObject().put("text", " hello ")).first)
        assertEquals(NfcWrite.Payload(null, "tel:+441234"), NfcWrite.payloadOf(JSONObject().put("uri", "tel:+441234")).first)
        val done = NfcWrite.written(byteArrayOf(0x01, 0x02), 12, formatted = true, text = "hello", uri = null)
        assertEquals("0102", done.getString("tag_id"))
        assertTrue(done.getBoolean("formatted"))
        assertEquals("the record needs 900 bytes and this tag holds 137", NfcWrite.tooBig(900, 137))
        // The reader is armed for a bounded time, on both actions.
        assertEquals(NfcRead.DEFAULT_TIMEOUT_S, ai.jarvis.app.automation.actions.builtin.NfcTimeout.of(JSONObject(), NfcRead.DEFAULT_TIMEOUT_S))
        assertNull(ai.jarvis.app.automation.actions.builtin.NfcTimeout.of(JSONObject().put("timeout_s", 2), NfcRead.DEFAULT_TIMEOUT_S))
        assertEquals(45, ai.jarvis.app.automation.actions.builtin.NfcTimeout.of(JSONObject().put("timeout_s", 45), NfcRead.DEFAULT_TIMEOUT_S))
        // The dispatcher's clock outlasts the longest wait either action allows.
        assertTrue(NfcRead.timeoutMs > ai.jarvis.app.automation.actions.builtin.NfcTimeout.MAX_S * 1000L)
        assertTrue(ScanCode.timeoutMs > ScanCode.MAX_TIMEOUT_S * 1000L)
    }
}
