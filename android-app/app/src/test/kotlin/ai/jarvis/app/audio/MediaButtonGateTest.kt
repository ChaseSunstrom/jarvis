package ai.jarvis.app.audio

import ai.jarvis.app.audio.MediaButtonGate.Action
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What the headset button may and may not do. Mirrored by
 * `tools/media_button_test.py`.
 */
class MediaButtonGateTest {

    private fun decide(
        headsetModeEnabled: Boolean = true,
        consentPending: Boolean = false,
        inConversation: Boolean = false,
        musicActive: Boolean = false,
        heldMs: Long = 50,
        msSinceLastAccepted: Long = Long.MAX_VALUE
    ) = MediaButtonGate.decide(
        headsetModeEnabled, consentPending, inConversation, musicActive,
        heldMs, msSinceLastAccepted
    )

    // --- rule 1: consent is unreachable from a button ----------------------

    @Test
    fun `every press is swallowed while a consent prompt is waiting`() {
        // The exhaustive version, because this is the invariant that matters:
        // no combination of the other inputs may produce anything but IGNORE.
        for (headsetMode in listOf(true, false)) {
            for (inConversation in listOf(true, false)) {
                for (musicActive in listOf(true, false)) {
                    for (held in listOf(0L, 50L, 600L, 5_000L)) {
                        for (since in listOf(0L, 349L, 350L, Long.MAX_VALUE)) {
                            val action = MediaButtonGate.decide(
                                headsetModeEnabled = headsetMode,
                                consentPending = true,
                                inConversation = inConversation,
                                musicActive = musicActive,
                                heldMs = held,
                                msSinceLastAccepted = since
                            )
                            assertEquals(
                                "a headset button reached a pending consent prompt " +
                                    "(headsetMode=$headsetMode, inConversation=$inConversation, " +
                                    "music=$musicActive, held=$held, since=$since)",
                                Action.IGNORE,
                                action
                            )
                        }
                    }
                }
            }
        }
    }

    @Test
    fun `the gate has no outcome that could approve anything`() {
        // A regression guard on the type itself: if someone adds an APPROVE-ish
        // action later, this test is where they find out it is not allowed.
        val names = Action.values().map { it.name }.toSet()
        assertEquals(setOf("IGNORE", "PASS_TO_MEDIA", "START_TURN", "END_TURN"), names)
    }

    @Test
    fun `a press during a prompt is not merely deduplicated`() {
        // Even a "fresh" press — one the debounce would have accepted — is
        // swallowed. Ordering the consent check first is what guarantees it.
        assertEquals(
            Action.IGNORE,
            decide(consentPending = true, msSinceLastAccepted = Long.MAX_VALUE)
        )
    }

    // --- rule 4: opt-out gives the button back -----------------------------

    @Test
    fun `with headset mode off the button belongs to the media app`() {
        assertEquals(Action.PASS_TO_MEDIA, decide(headsetModeEnabled = false))
        assertEquals(
            Action.PASS_TO_MEDIA,
            decide(headsetModeEnabled = false, musicActive = true, heldMs = 5_000)
        )
    }

    // --- rule 3: debounce --------------------------------------------------

    @Test
    fun `a bounced press is dropped`() {
        assertEquals(Action.IGNORE, decide(msSinceLastAccepted = 0))
        assertEquals(Action.IGNORE, decide(msSinceLastAccepted = MediaButtonGate.DEBOUNCE_MS - 1))
    }

    @Test
    fun `a deliberate second press is not`() {
        assertEquals(Action.START_TURN, decide(msSinceLastAccepted = MediaButtonGate.DEBOUNCE_MS))
    }

    @Test
    fun `only presses Jarvis acted on reset the debounce clock`() {
        // Double-tap-to-skip must keep working in the user's music player.
        assertFalse(MediaButtonGate.resetsDebounce(Action.PASS_TO_MEDIA))
        assertFalse(MediaButtonGate.resetsDebounce(Action.IGNORE))
        assertTrue(MediaButtonGate.resetsDebounce(Action.START_TURN))
        assertTrue(MediaButtonGate.resetsDebounce(Action.END_TURN))
    }

    // --- rule 2: do not steal play/pause -----------------------------------

    @Test
    fun `a tap while music is playing means pause, not Jarvis`() {
        assertEquals(Action.PASS_TO_MEDIA, decide(musicActive = true, heldMs = 50))
    }

    @Test
    fun `a long press summons Jarvis over playing music`() {
        assertEquals(
            Action.START_TURN,
            decide(musicActive = true, heldMs = MediaButtonGate.LONG_PRESS_MS)
        )
    }

    @Test
    fun `a tap in silence summons Jarvis`() {
        assertEquals(Action.START_TURN, decide(musicActive = false))
    }

    // --- mid-conversation --------------------------------------------------

    @Test
    fun `the button ends the turn once Jarvis is listening`() {
        assertEquals(Action.END_TURN, decide(inConversation = true))
        assertEquals(
            "a long press mid-turn is how you cut Jarvis off",
            Action.END_TURN,
            decide(inConversation = true, heldMs = 5_000)
        )
        assertEquals(
            "ending a turn beats handing the button to a media app",
            Action.END_TURN,
            decide(inConversation = true, musicActive = true)
        )
    }

    @Test
    fun `a press mid-conversation never starts a second turn`() {
        for (music in listOf(true, false)) {
            for (held in listOf(0L, 599L, 600L, 5_000L)) {
                assertNotEquals(
                    Action.START_TURN,
                    decide(inConversation = true, musicActive = music, heldMs = held)
                )
            }
        }
    }
}
