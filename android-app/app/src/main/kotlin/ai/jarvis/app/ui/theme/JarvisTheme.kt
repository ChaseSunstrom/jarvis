package ai.jarvis.app.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * @generated from design/tokens.json — DO NOT EDIT. Run `python3 design/build.py`.
 *
 * The Compose theme, built from [JarvisTokens]. Reactor II is one dark world,
 * so there is one colour scheme and no light variant: a Composable that reads
 * `MaterialTheme.colorScheme` gets the same palette the console and the desktop
 * draw with.
 */
object JarvisColors {
    val Bg = Color(JarvisTokens.Color.BG)
    val Panel = Color(JarvisTokens.Color.PANEL)
    val Surface2 = Color(JarvisTokens.Color.SURFACE_2)
    val Accent = Color(JarvisTokens.Color.ACCENT)
    val AccentDeep = Color(JarvisTokens.Color.ACCENT_DEEP)
    val AccentInk = Color(JarvisTokens.Color.ACCENT_INK)
    val Warn = Color(JarvisTokens.Color.WARN)
    val Danger = Color(JarvisTokens.Color.DANGER)
    val Ok = Color(JarvisTokens.Color.OK)
    val Text = Color(JarvisTokens.Color.TEXT)
    val TextBright = Color(JarvisTokens.Color.TEXT_BRIGHT)
    val TextDim = Color(JarvisTokens.Color.TEXT_DIM)
    val TextFaint = Color(JarvisTokens.Color.TEXT_FAINT)
    val Line = Color(JarvisTokens.Color.LINE)
    val LineSoft = Color(JarvisTokens.Color.LINE_SOFT)
}

val JarvisColorScheme = darkColorScheme(
    primary = JarvisColors.Accent,
    onPrimary = JarvisColors.AccentInk,
    secondary = JarvisColors.AccentDeep,
    onSecondary = JarvisColors.AccentInk,
    tertiary = JarvisColors.Warn,
    onTertiary = JarvisColors.AccentInk,
    background = JarvisColors.Bg,
    onBackground = JarvisColors.Text,
    surface = JarvisColors.Panel,
    onSurface = JarvisColors.Text,
    surfaceVariant = JarvisColors.Surface2,
    onSurfaceVariant = JarvisColors.TextDim,
    outline = JarvisColors.Line,
    outlineVariant = JarvisColors.LineSoft,
    error = JarvisColors.Danger,
    onError = JarvisColors.AccentInk,
)

val JarvisTypography = Typography(
    displayLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Light, fontSize = 40.sp),
    titleLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Medium, fontSize = JarvisTokens.Type.TITLE.sp),
    bodyLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = JarvisTokens.Type.RESPONSE.sp),
    bodyMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = JarvisTokens.Type.BODY.sp),
    bodySmall = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = JarvisTokens.Type.HINT.sp),
    labelLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Medium, fontSize = JarvisTokens.Type.FIELD.sp),
    labelMedium = TextStyle(fontFamily = FontFamily.Monospace, fontSize = JarvisTokens.Type.MONO.sp),
    labelSmall = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Medium, fontSize = JarvisTokens.Type.LABEL.sp, letterSpacing = 0.16.sp),
)

val JarvisShapes = Shapes(
    small = RoundedCornerShape(JarvisTokens.Radius.SM.dp),
    medium = RoundedCornerShape(JarvisTokens.Radius.MD.dp),
    large = RoundedCornerShape(JarvisTokens.Radius.LG.dp),
)

@Composable
fun JarvisTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = JarvisColorScheme,
        typography = JarvisTypography,
        shapes = JarvisShapes,
        content = content,
    )
}
