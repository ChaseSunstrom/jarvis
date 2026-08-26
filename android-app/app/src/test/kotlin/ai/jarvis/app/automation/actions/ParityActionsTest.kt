package ai.jarvis.app.automation.actions

import ai.jarvis.app.automation.actions.builtin.Builtins
import ai.jarvis.app.automation.actions.builtin.GetNetworkInfo
import ai.jarvis.app.automation.actions.builtin.MediaControl
import ai.jarvis.app.automation.actions.builtin.MediaNowPlaying
import ai.jarvis.app.automation.actions.builtin.PlayMedia
import ai.jarvis.app.automation.actions.builtin.RecordAudio
import ai.jarvis.app.automation.actions.builtin.SetBluetooth
import ai.jarvis.app.automation.actions.builtin.SetWallpaper
import ai.jarvis.app.automation.actions.builtin.ParityActions
import ai.jarvis.app.automation.actions.builtin.SendIntent
import ai.jarvis.app.automation.actions.builtin.SetScreenTimeout
import ai.jarvis.app.automation.actions.builtin.ShowToast
import ai.jarvis.app.automation.actions.builtin.UiActions
import ai.jarvis.app.automation.policy.ActionTier
import android.view.KeyEvent
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Tasker rows closed by M61, proved where a JVM can prove them: the
 * arithmetic of each action — what a parameter means and what is refused —
 * and that every one is registered at the tier the parity table states.
 * What a handset does with them is `docs/ANDROID_DEVICE_TESTS.md` ADT-036+.
 */
class ParityActionsTest {

    @Test
    fun `the eight rows M61 closed are these, in the table's order`() {
        assertEquals(
            listOf(
                "show_toast", "set_auto_brightness", "set_rotation_lock", "set_screen_timeout",
                "get_network_info", "send_intent", "launch_shortcut", "media_control",
                "media_now_playing", "play_media", "set_wallpaper", "record_audio", "set_bluetooth",
            ),
            ParityActions.all.map { it.id },
        )
    }

    @Test
    fun `every parity action is registered once at its stated tier`() {
        val ids = Builtins.all().map { it.id }
        for (action in ParityActions.all) {
            assertEquals("${action.id} registered once", 1, ids.count { it == action.id })
        }
        assertEquals(ActionTier.AUTO, ShowToast.tier)
        assertEquals(ActionTier.CONFIRM, SendIntent.tier)
        assertEquals(ActionTier.AUTO, MediaControl.tier)
        // launch_shortcut needs both halves of a shortcut's identity; its tier is direct.
        val shortcut = Builtins.all().first { it.id == "launch_shortcut" }
        assertEquals(ActionTier.AUTO, shortcut.tier)
        assertEquals(setOf("package", "shortcut_id"), shortcut.paramsSchema.keys)
        // lock_screen and screenshot are the accessibility agent's, under its ids.
        assertTrue(ParityActions.aliases["screenshot"] == "take_screenshot")
        assertTrue(ParityActions.aliases["lock_screen"] == "ui_global_action")
        assertTrue(UiActions.all.any { it.id == "take_screenshot" })
    }

    @Test
    fun `media_control maps words to the media keys and refuses the rest`() {
        assertEquals(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, MediaControl.keyFor("toggle"))
        assertEquals(KeyEvent.KEYCODE_MEDIA_NEXT, MediaControl.keyFor(" Next "))
        assertEquals(KeyEvent.KEYCODE_MEDIA_PREVIOUS, MediaControl.keyFor("prev"))
        assertEquals(KeyEvent.KEYCODE_MEDIA_STOP, MediaControl.keyFor("stop"))
        assertNull(MediaControl.keyFor("louder"))
        assertNull(MediaControl.keyFor(null))
    }

    @Test
    fun `show_toast needs text and keeps it short`() {
        assertNull(ShowToast.textOf(JSONObject()))
        assertNull(ShowToast.textOf(JSONObject().put("text", "   ")))
        assertEquals("Hello", ShowToast.textOf(JSONObject().put("text", " Hello ")))
        assertEquals(ShowToast.MAX_CHARS, ShowToast.textOf(JSONObject().put("text", "x".repeat(500)))!!.length)
    }

    @Test
    fun `set_screen_timeout accepts only what a phone accepts`() {
        assertNull(SetScreenTimeout.secondsOf(JSONObject()))
        assertNull(SetScreenTimeout.secondsOf(JSONObject().put("seconds", 5)))
        assertNull(SetScreenTimeout.secondsOf(JSONObject().put("seconds", 100000)))
        assertEquals(120, SetScreenTimeout.secondsOf(JSONObject().put("seconds", 120)))
    }

    @Test
    fun `send_intent refuses a bare word and keeps the extras in order`() {
        assertEquals("action is required", SendIntent.parse(JSONObject()).second)
        assertTrue(SendIntent.parse(JSONObject().put("action", "VIEW")).second!!.contains("fully qualified"))
        val (parsed, error) = SendIntent.parse(
            JSONObject()
                .put("action", "android.intent.action.VIEW")
                .put("data", "https://example.invalid/x")
                .put("package", "com.android.chrome")
                .put("extras", JSONObject().put("b", "2").put("a", "1"))
        )
        assertNull(error)
        assertEquals("android.intent.action.VIEW", parsed!!.action)
        assertEquals("com.android.chrome", parsed.pkg)
        assertEquals(listOf("b", "a"), parsed.extras.keys.toList())
    }

    @Test
    fun `get_network_info names the transport and never guesses a hidden ssid`() {
        assertEquals("vpn", GetNetworkInfo.transportOf(wifi = true, cellular = false, ethernet = false, vpn = true))
        assertEquals("wifi", GetNetworkInfo.transportOf(wifi = true, cellular = true, ethernet = false, vpn = false))
        assertEquals("none", GetNetworkInfo.transportOf(wifi = false, cellular = false, ethernet = false, vpn = false))
        assertEquals("Home", GetNetworkInfo.ssidOf("\"Home\""))
        assertNull(GetNetworkInfo.ssidOf("<unknown ssid>"))
        assertNull(GetNetworkInfo.ssidOf(null))
    }

    @Test
    fun `media_now_playing says what it knows and never invents a title`() {
        assertEquals("nothing is playing", MediaNowPlaying.describe(null, null, null, false))
        assertEquals("something is playing in com.spotify.music", MediaNowPlaying.describe("", null, "com.spotify.music", true))
        assertEquals("playing Blue Monday — New Order in com.spotify.music", MediaNowPlaying.describe("Blue Monday", "New Order", "com.spotify.music", true))
        assertEquals("paused: Blue Monday", MediaNowPlaying.describe("Blue Monday", " ", null, false))
    }

    @Test
    fun `play_media takes a web url or a sandbox file and nothing else`() {
        assertTrue(PlayMedia.sourceOf("https://example.invalid/a.mp3") is PlayMedia.Source.Url)
        assertEquals(PlayMedia.Source.SandboxFile("sounds/chime.mp3"), PlayMedia.sourceOf("sounds/chime.mp3"))
        assertTrue(PlayMedia.sourceOf("../../etc/passwd") is PlayMedia.Source.Rejected)
        assertTrue(PlayMedia.sourceOf("file:///sdcard/x.mp3") is PlayMedia.Source.Rejected)
        assertTrue(PlayMedia.sourceOf("") is PlayMedia.Source.Rejected)
    }

    @Test
    fun `set_wallpaper knows three screens and record_audio a sane length`() {
        assertEquals(android.app.WallpaperManager.FLAG_SYSTEM, SetWallpaper.flagsFor(null))
        assertEquals(android.app.WallpaperManager.FLAG_LOCK, SetWallpaper.flagsFor("lock"))
        assertNull(SetWallpaper.flagsFor("fridge"))
        assertEquals(ActionTier.NOTIFY, SetWallpaper.tier)
        assertEquals(ActionTier.CONFIRM, RecordAudio.tier)
        assertNull(RecordAudio.secondsOf(JSONObject().put("seconds", 0)))
        assertNull(RecordAudio.secondsOf(JSONObject().put("seconds", 3600)))
        assertEquals(10, RecordAudio.secondsOf(JSONObject().put("seconds", 10)))
    }

    @Test
    fun `set_bluetooth flips the radio itself only where Android still allows`() {
        assertTrue(SetBluetooth.directOn(android.os.Build.VERSION_CODES.S_V2))
        assertTrue(!SetBluetooth.directOn(android.os.Build.VERSION_CODES.TIRAMISU))
        assertEquals(ActionTier.NOTIFY, SetBluetooth.tier)
    }
}
