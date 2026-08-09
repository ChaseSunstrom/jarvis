package ai.jarvis.app.config

import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The wake-word gate decides when the microphone is open on a phone in
 * someone's pocket, so "away from home and not in the car" must be provably
 * silent rather than approximately silent.
 */
class WakeWordGateTest {

    private val gate = WakeWordGate()

    @Test
    fun carBluetoothBeatsTheClock() {
        assertTrue(gate.shouldListen(isHome = false, carBtConnected = true, hour = 3))
        assertTrue(gate.shouldListen(isHome = true, carBtConnected = true, hour = 3))
    }

    @Test
    fun awayFromHomeWithNoCarNeverListens() {
        for (hour in 0..23) {
            assertFalse(
                "should be silent at hour $hour",
                gate.shouldListen(isHome = false, carBtConnected = false, hour = hour)
            )
        }
    }

    @Test
    fun atHomeListensOnlyDuringWakingHours() {
        assertTrue(gate.shouldListen(isHome = true, carBtConnected = false, hour = 7))
        assertTrue(gate.shouldListen(isHome = true, carBtConnected = false, hour = 22))
        assertFalse(gate.shouldListen(isHome = true, carBtConnected = false, hour = 23))
        assertFalse(gate.shouldListen(isHome = true, carBtConnected = false, hour = 6))
        assertFalse(gate.shouldListen(isHome = true, carBtConnected = false, hour = 3))
    }

    @Test
    fun windowsWrappingMidnightWork() {
        val night = WakeWordGate(22, 6)
        assertTrue(night.isWakingHour(22))
        assertTrue(night.isWakingHour(23))
        assertTrue(night.isWakingHour(0))
        assertTrue(night.isWakingHour(5))
        assertFalse(night.isWakingHour(6))
        assertFalse(night.isWakingHour(12))
    }

    @Test
    fun outOfRangeInputsAreRejected() {
        assertThrows(IllegalArgumentException::class.java) { gate.isWakingHour(24) }
        assertThrows(IllegalArgumentException::class.java) { gate.shouldListen(true, false, -1) }
        assertThrows(IllegalArgumentException::class.java) { WakeWordGate(24, 12) }
        assertThrows(IllegalArgumentException::class.java) { WakeWordGate(1, 25) }
    }
}
