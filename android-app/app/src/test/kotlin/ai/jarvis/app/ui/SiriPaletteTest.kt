package ai.jarvis.app.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What the floating orb wears in each state.
 *
 * "It changes colour" is a behaviour the user can see and describe — it was
 * half of the report that produced this surface — so the table is a table
 * rather than magic numbers inside a `draw` method, and it is checked for the
 * properties that make it legible: every state has one, no two states share
 * one, and nothing is transparent (the view sets its own alpha from the
 * microphone level, so a colour that arrived pre-faded would be faded twice).
 */
class SiriPaletteTest {

    private val tones = SiriPalette.Tone.values()

    @Test
    fun everyStateHasAFullSetOfBlobs() {
        for (tone in tones) {
            assertEquals(
                "$tone draws SiriPalette.BLOB_COUNT blobs",
                SiriPalette.BLOB_COUNT,
                SiriPalette.blobs(tone).size,
            )
        }
    }

    @Test
    fun everyColourIsFullyOpaque() {
        for (tone in tones) {
            for (color in SiriPalette.blobs(tone) + SiriPalette.core(tone) + SiriPalette.rim(tone)) {
                assertEquals(
                    "$tone has a colour that is not fully opaque: ${Integer.toHexString(color)}",
                    0xFF,
                    (color ushr 24) and 0xFF,
                )
            }
        }
    }

    @Test
    fun noTwoStatesLookTheSame() {
        val seen = mutableMapOf<List<Int>, SiriPalette.Tone>()
        for (tone in tones) {
            val key = SiriPalette.blobs(tone).toList()
            val clash = seen[key]
            assertEquals("$tone and $clash are indistinguishable", null, clash)
            seen[key] = tone
        }
    }

    @Test
    fun thinkingIsNotMistakableForAnError() {
        // Amber and red are the two a glance most easily confuses, and one of
        // them means something went wrong in the user's house.
        assertNotEquals(
            SiriPalette.blobs(SiriPalette.Tone.THINKING)[0],
            SiriPalette.blobs(SiriPalette.Tone.ERROR)[0],
        )
    }

    @Test
    fun theRimIsOneOfTheBlobColours() {
        // The ring at the edge has to belong to the ball it is drawn around.
        for (tone in tones) {
            assertTrue(
                "$tone's rim is not one of its own colours",
                SiriPalette.rim(tone) in SiriPalette.blobs(tone),
            )
        }
    }

    @Test
    fun everyStateMoves() {
        for (tone in tones) {
            assertTrue("$tone would be a still picture", SiriPalette.orbitHz(tone) > 0f)
        }
    }

    @Test
    fun restingIsTheCalmestState() {
        // If idle span faster than listening, the orb would look busier doing
        // nothing than it does hearing you.
        for (tone in tones) {
            if (tone == SiriPalette.Tone.IDLE) continue
            assertTrue(
                "idle is not calmer than $tone",
                SiriPalette.orbitHz(SiriPalette.Tone.IDLE) < SiriPalette.orbitHz(tone),
            )
        }
    }
}
