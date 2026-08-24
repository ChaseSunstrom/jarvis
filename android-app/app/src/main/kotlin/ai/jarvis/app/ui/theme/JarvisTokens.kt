package ai.jarvis.app.ui.theme

/**
 * @generated from design/tokens.json — DO NOT EDIT. Run `python3 design/build.py`.
 *
 * The design tokens for the phone's Views. `JarvisUi` aliases these under the
 * names its builders use; `JarvisTheme` turns them into a Compose theme;
 * `android-app/tools/design_token_test.py` reads them back against the source.
 */
object JarvisTokens {
    object Color {
        const val BG = 0xFF03070B.toInt() // --jv-bg
        const val BG_RAISED = 0xFF050B11.toInt() // --jv-bg-raised
        const val PANEL = 0xFF070F16.toInt() // --jv-panel
        const val PANEL_SOLID = 0xFF070F16.toInt() // --jv-panel-solid
        const val SURFACE_2 = 0xFF0A141C.toInt() // --jv-surface-2
        const val FIELD = 0xFF050B11.toInt() // --jv-field
        const val SURFACE_SUNKEN = 0xFF030709.toInt() // --jv-surface-sunken
        const val ACCENT = 0xFF4FE3FF.toInt() // --jv-accent
        const val ACCENT_DEEP = 0xFF1FA9C9.toInt() // --jv-accent-deep
        const val ACCENT_LIFT = 0xFF7EE6FF.toInt() // --jv-accent-lift
        const val ACCENT_INK = 0xFF031016.toInt() // --jv-accent-ink
        const val WARN = 0xFFF2B84B.toInt() // --jv-warn
        const val AMBER = 0xFFFF9E2C.toInt() // --jv-amber
        const val GOLD = 0xFFFFCF5C.toInt() // --jv-gold
        const val DANGER = 0xFFFF6B5C.toInt() // --jv-danger
        const val DANGER_TEXT = 0xFFFF9184.toInt() // --jv-danger-text
        const val OK = 0xFF6FF2C0.toInt() // --jv-ok
        const val TEXT = 0xFFD3E6EC.toInt() // --jv-text
        const val TEXT_BRIGHT = 0xFFF1FAFC.toInt() // --jv-text-bright
        const val TEXT_DIM = 0xFF7C9EA9.toInt() // --jv-text-dim
        const val TEXT_FAINT = 0xFF6F8D99.toInt() // --jv-text-faint
        const val TICK = 0xFF455F68.toInt() // --jv-tick
        const val LINE = 0xFF16323F.toInt() // --jv-line
        const val LINE_SOFT = 0xFF0F2430.toInt() // --jv-line-soft
        const val LINE_HAIR = 0xFF0B1B23.toInt() // --jv-line-hair
        const val WASH = 0x144FE3FF.toInt() // --jv-wash
        const val WASH_STRONG = 0x2E4FE3FF.toInt() // --jv-wash-strong
        const val GLOW = 0x474FE3FF.toInt() // --jv-glow
        const val FOCUS = 0xFF4FE3FF.toInt() // --jv-focus
        const val ORB_SUBSTRATE = 0xFF060B16.toInt() // --jv-orb-substrate
        const val ORB_HOUSING = 0xFF01030A.toInt() // --jv-orb-housing
        const val ORB_HUB_METAL = 0xFFA8BDD2.toInt() // --jv-orb-hub-metal
        const val ORB_IDLE_BLOB_0 = 0xFF2BB0D8.toInt() // --jv-orb-idle-blob-0
        const val ORB_IDLE_BLOB_1 = 0xFF3A6FE0.toInt() // --jv-orb-idle-blob-1
        const val ORB_IDLE_BLOB_2 = 0xFF29D8C0.toInt() // --jv-orb-idle-blob-2
        const val ORB_IDLE_CORE = 0xFFDFF6FF.toInt() // --jv-orb-idle-core
        const val ORB_LISTENING_BLOB_0 = 0xFF3FD8FF.toInt() // --jv-orb-listening-blob-0
        const val ORB_LISTENING_BLOB_1 = 0xFF5A8CFF.toInt() // --jv-orb-listening-blob-1
        const val ORB_LISTENING_BLOB_2 = 0xFF54FFE0.toInt() // --jv-orb-listening-blob-2
        const val ORB_LISTENING_CORE = 0xFFEBFDFF.toInt() // --jv-orb-listening-core
        const val ORB_THINKING_BLOB_0 = 0xFFFF9E2C.toInt() // --jv-orb-thinking-blob-0
        const val ORB_THINKING_BLOB_1 = 0xFFFF5FA2.toInt() // --jv-orb-thinking-blob-1
        const val ORB_THINKING_BLOB_2 = 0xFFC46BFF.toInt() // --jv-orb-thinking-blob-2
        const val ORB_THINKING_CORE = 0xFFFFE9CC.toInt() // --jv-orb-thinking-core
        const val ORB_SPEAKING_BLOB_0 = 0xFFFFCF5C.toInt() // --jv-orb-speaking-blob-0
        const val ORB_SPEAKING_BLOB_1 = 0xFFFF9A3C.toInt() // --jv-orb-speaking-blob-1
        const val ORB_SPEAKING_BLOB_2 = 0xFFFF7BC0.toInt() // --jv-orb-speaking-blob-2
        const val ORB_SPEAKING_CORE = 0xFFFFF3D2.toInt() // --jv-orb-speaking-core
        const val ORB_ERROR_BLOB_0 = 0xFFFF6B5C.toInt() // --jv-orb-error-blob-0
        const val ORB_ERROR_BLOB_1 = 0xFFE0344B.toInt() // --jv-orb-error-blob-1
        const val ORB_ERROR_BLOB_2 = 0xFFFF9A6B.toInt() // --jv-orb-error-blob-2
        const val ORB_ERROR_CORE = 0xFFFFD9D2.toInt() // --jv-orb-error-core
        const val PAPER = 0xFFFFFFFF.toInt() // --jv-paper
        const val TEXT_DIM_80 = 0xCC7C9EA9.toInt() // --jv-text-dim at 80%
        const val SCRIM = 0xE603070B.toInt() // --jv-bg at 90%
    }

    /** The type scale in sp, named for the job. */
    object Type {
        const val LABEL = 11f
        const val HINT = 12f
        const val MONO = 13f
        const val BODY = 14f
        const val FIELD = 15f
        const val RESPONSE = 20f
        const val TITLE = 22f
    }

    /** The spacing scale in dp, named for the job. */
    object Space {
        const val HAIRLINE = 1
        const val TIGHT = 4
        const val SNUG = 6
        const val ROW = 10
        const val GAP = 12
        const val SECTION = 16
        const val SCREEN = 20
        const val WIDE = 24
    }

    /** Corner radii in dp. */
    object Radius {
        const val SM = 3
        const val MD = 6
        const val LG = 12
        const val PILL = 999
    }
}
