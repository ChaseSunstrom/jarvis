#!/usr/bin/env python3
"""Generate every surface's design tokens from ``design/tokens.json``.

``design/tokens.json`` is the only file where a colour, size, radius, shadow or
duration is typed by a human. This script turns it into what each surface
actually consumes, and ``--check`` refuses to let any of those outputs drift:

    jarvis-web/src/lib/styles/tokens.css          CSS custom properties on :root
    jarvis-web/src/lib/tokens.ts                  the same table for TypeScript
    jarvis-desktop/jarvis_desktop/tokens.py       the same table for the agent
    android-app/.../ui/theme/JarvisTokens.kt      const vals for the Views
    android-app/.../ui/theme/JarvisTheme.kt       a Compose MaterialTheme
    android-app/app/src/main/res/values/tokens.xml colour + dimen resources
    android-app/app/src/main/res/values/colors.xml aliases the themes read

Two files are checked but not rewritten, because a 1,400-line executable spec
(``android-app/tools/reactor_orb_test.py``) already pins them to each other and
to the shader's arithmetic: ``SiriPalette.kt`` and the palette comments in
``Orb.svelte``. Their values must equal ``color.orb.*`` here; ``--check`` says
which one moved.

    python3 design/build.py            write every output
    python3 design/build.py --check    exit 1 if any output or pinned file differs

Stdlib only, so it runs wherever python3 does — including CI's ``static`` job.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "design" / "tokens.json"
MARK = "@generated from design/tokens.json"

WEB_CSS = ROOT / "jarvis-web/src/lib/styles/tokens.css"
WEB_TS = ROOT / "jarvis-web/src/lib/tokens.ts"
DESKTOP_PY = ROOT / "jarvis-desktop/jarvis_desktop/tokens.py"
KT_DIR = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app/ui/theme"
KT_TOKENS = KT_DIR / "JarvisTokens.kt"
KT_THEME = KT_DIR / "JarvisTheme.kt"
RES = ROOT / "android-app/app/src/main/res/values"
XML_TOKENS = RES / "tokens.xml"
XML_COLORS = RES / "colors.xml"
SIRI = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app/ui/SiriPalette.kt"
ORB = ROOT / "jarvis-web/src/lib/components/Orb.svelte"

#: Desktop constant -> token. ``theme.py`` imports these names from ``tokens.py``;
#: ``tests/test_theme.py`` reads the same lines back and checks AA contrast.
DESKTOP_COLOURS = {
    "BG": "bg", "PANEL": "panel-solid", "ACCENT": "accent", "ACCENT_DEEP": "accent-deep",
    "ACCENT_INK": "accent-ink", "TEXT": "text", "TEXT_BRIGHT": "text-bright",
    "TEXT_DIM": "text-dim", "TEXT_FAINT": "text-faint", "OK": "ok", "DANGER": "danger",
    "WARN": "warn",
}
#: Android colours that are a token at partial alpha. Kotlin carries alpha in the
#: int, so these are generated rather than composed at the call site.
#: Android constants derived from a token at an alpha.
#:
#: Every one of these was a literal in the Kotlin — `0x553FD8FF`, `0xF0000308`,
#: `0x22FF9E2C` — which is a colour nobody can find from `design/tokens.json`
#: and nobody notices when the palette moves. Named here, they move with it.
#: The alphas are the ones the app actually used; the RGB is the token's, so a
#: few of these shift by a shade or two from the hand-mixed originals. The
#: screenshot goldens are what that shift is reviewed in.
ANDROID_DERIVED = {
    "TEXT_DIM_80": ("text-dim", 0xCC),
    "SCRIM": ("bg", 0xE6),
    #: The overlay behind a sheet, and the one behind an approval — heavier,
    #: because what is under it must not compete with a decision.
    "SCRIM_HEAVY": ("bg", 0xF0),
    "SCRIM_APPROVAL": ("bg", 0xF2),
    #: The accent, at the four weights the chrome uses it: a hairline stroke, a
    #: resting border, a lit border, a filled chip.
    "ACCENT_13": ("accent", 0x22),
    "ACCENT_20": ("accent", 0x33),
    "ACCENT_27": ("accent", 0x44),
    "ACCENT_33": ("accent", 0x55),
    #: Warn, likewise: the banner's fill, its border, and the text on it.
    "WARN_13": ("warn", 0x22),
    "WARN_40": ("warn", 0x66),
    "WARN_53": ("warn", 0x88),
    #: A progress track, which is the one thing here that is grey rather than
    #: coloured: it is the ABSENCE of progress.
    "TRACK": ("text-faint", 0x33),
    #: The panel a floating sheet is made of, at the two opacities in use.
    "PANEL_94": ("panel", 0xF0),
}
#: ``colors.xml`` names the platform themes read -> token resource.
XML_ALIASES = {
    "jarvis_bg": "jv_bg", "jarvis_surface": "jv_panel", "jarvis_accent": "jv_accent",
    "jarvis_dim": "jv_text_dim_80", "jarvis_faint": "jv_text_faint", "jarvis_amber": "jv_amber",
    "jarvis_gold": "jv_gold", "jarvis_approve": "jv_ok", "jarvis_deny": "jv_danger",
    "jarvis_approval_scrim": "jv_scrim",
}


# --- reading the source -------------------------------------------------------


def load() -> dict:
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def leaves(node: dict, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str, object]]:
    """Every ``{"$type", "$value"}`` leaf as ``(path, type, value)``, in file order."""
    out = []
    if "$value" in node:
        return [(path, node.get("$type", "other"), node["$value"])]
    for key, value in node.items():
        if key.startswith("$") or not isinstance(value, dict):
            continue
        out.extend(leaves(value, path + (key,)))
    return out


def resolve(tokens: dict, value: object) -> object:
    """``"2px solid {color.focus}"`` -> ``"2px solid #4fe3ff"``. Aliases may nest."""
    if not isinstance(value, str) or "{" not in value:
        return value

    def lookup(match: re.Match) -> str:
        node = tokens
        for part in match.group(1).split("."):
            node = node[part]
        return str(resolve(tokens, node["$value"]))

    return re.sub(r"\{([a-z0-9.-]+)\}", lookup, value)


def css_name(path: tuple[str, ...]) -> str | None:
    """The console's ``--jv-*`` name for a token path, or None if the token is
    phone-only. The shorthands are the names the console already used before
    the source of truth moved, so nothing downstream had to be renamed."""
    group, rest = path[0], path[1:]
    if "android" in rest:
        return None
    if group == "color":
        return "--jv-" + "-".join(rest)
    if group == "type":
        kind, name = rest[0], "-".join(rest[1:])
        prefix = {"family": "font", "size": "fs", "weight": "weight", "tracking": "track",
                  "relative": "rel"}[kind]
        return f"--jv-{prefix}-{name}"
    if group == "space":
        return "--jv-space-" + "-".join(rest)
    if group == "radius":
        return "--jv-radius-" + "-".join(rest)
    if group == "elevation":
        name = "-".join(rest)
        return f"--jv-{name}" if name.startswith("glow-") else f"--jv-elev-{name}"
    if group == "motion":
        kind = rest[0]
        if kind == "drift":
            return "--jv-drift"
        prefix = {"dur": "dur", "ease": "ease", "stagger": "stagger", "reactor": "rx", "ambient": "amb"}[kind]
        return f"--jv-{prefix}-" + "-".join(rest[1:])
    if group == "chrome":
        return "--jv-" + "-".join(rest)
    raise ValueError(f"unknown token group {group!r}")


def css_value(kind: str, value: object) -> str:
    if kind == "fontFamily":
        names = []
        for name in value:
            quoted = " " in name or (any(c.isupper() for c in name) and "-" in name)
            names.append(f"'{name}'" if quoted else name)
        return ", ".join(names)
    return str(value)


def web_tokens(tokens: dict) -> list[tuple[str, str, tuple[str, ...]]]:
    out = []
    for path, kind, value in leaves(tokens):
        name = css_name(path)
        if name is None:
            continue
        out.append((name, css_value(kind, resolve(tokens, value)), path))
    return out


# --- colour helpers ------------------------------------------------------------


def argb(value: str, alpha: int | None = None) -> str:
    """``#rrggbb`` or ``rgba(r, g, b, a)`` -> ``AARRGGBB`` (upper-case hex)."""
    if value.startswith("#"):
        rgb = value[1:]
        a = 0xFF if alpha is None else alpha
        return f"{a:02X}{rgb.upper()}"
    m = re.fullmatch(r"rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\s*\)", value)
    if not m:
        raise ValueError(f"not a colour: {value}")
    r, g, b = (int(m.group(i)) for i in (1, 2, 3))
    a = round(float(m.group(4) or 1) * 255) if alpha is None else alpha
    return f"{a:02X}{r:02X}{g:02X}{b:02X}"


def const_name(path: tuple[str, ...]) -> str:
    return "_".join(path).upper().replace("-", "_")


# --- outputs ------------------------------------------------------------------


def gen_css(tokens: dict) -> str:
    lines = [
        "/*",
        f" * {MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        " *",
        " * Jarvis design tokens (Reactor II). Every colour, size, duration and glow the",
        " * HUD and the console use is declared here once; `src/lib/tokens.ts` is the",
        " * same table for JavaScript and `tokens.test.ts` fails if they drift. Nothing",
        " * else in the app may write a raw value — `scripts/verify/token_lint.py`",
        " * enforces that. Format: one declaration per line, `\\t--name: value;`.",
        " */",
        ":root {",
    ]
    last_group = None
    for name, value, path in web_tokens(tokens):
        group = path[0] if path[0] != "color" or len(path) < 3 else "color/" + path[1]
        if group != last_group:
            lines.append(f"\t/* {group} */")
            last_group = group
        lines.append(f"\t{name}: {value};")
    lines.append("}")
    return "\n".join(lines) + "\n"


TS_TAIL = '''
export type TokenName = keyof typeof TOKENS;

/** Look a token up by name. Throws on a typo rather than emitting `undefined`. */
export function token(name: TokenName): string {
	const value = TOKENS[name];
	if (value === undefined) throw new Error(`unknown design token ${name}`);
	return value;
}

/** `var(--jv-…)`, for inline styles that want the live (overridable) value. */
export function cssVar(name: TokenName): string {
	return `var(${name})`;
}

/**
 * The HUD's accent per pipeline state. The orb, the grid, the brackets and the
 * glow all derive from this one colour, which is why it lives in JS: CSS cannot
 * pick it from `data-state` without repeating every rule five times.
 */
export const STATE_ACCENT = {
	idle: TOKENS['--jv-accent-deep'],
	listening: TOKENS['--jv-accent'],
	thinking: TOKENS['--jv-amber'],
	speaking: TOKENS['--jv-gold'],
	error: TOKENS['--jv-danger']
} as const;

export type AccentState = keyof typeof STATE_ACCENT;

export function accentFor(state: string, isError = false): string {
	if (isError) return STATE_ACCENT.error;
	return STATE_ACCENT[state as AccentState] ?? STATE_ACCENT.idle;
}

/** A duration token as a number of milliseconds (`'260ms'` → 260, `'3.4s'` → 3400). */
export function tokenMs(name: TokenName): number {
	const value = TOKENS[name];
	const m = /^([\\d.]+)(ms|s)$/.exec(value);
	if (!m) throw new Error(`${name} is not a duration: ${value}`);
	return m[2] === 's' ? Number(m[1]) * 1000 : Number(m[1]);
}
'''


def gen_ts(tokens: dict) -> str:
    lines = [
        f"// {MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        "//",
        "// The design tokens, as data. `src/lib/styles/tokens.css` declares the same",
        "// names on `:root`; this module is what TypeScript reads when a value has to",
        "// reach JavaScript. `tokens.test.ts` diffs the two.",
        "",
        "/** Every `--jv-*` custom property, in the order tokens.css declares them. */",
        "export const TOKENS = {",
    ]
    entries = web_tokens(tokens)
    for i, (name, value, _path) in enumerate(entries):
        quoted = json.dumps(value) if "'" in value else f"'{value}'"
        lines.append(f"\t'{name}': {quoted}{',' if i < len(entries) - 1 else ''}")
    lines.append("} as const;")
    return "\n".join(lines) + "\n" + TS_TAIL


def gen_py(tokens: dict) -> str:
    lines = [
        f"# {MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        '"""The design tokens the desktop agent draws with.',
        "",
        "``theme.py`` imports the palette from here; ``tests/test_theme.py`` reads the",
        "``NAME = \"#rrggbb\"  # --jv-token`` lines back and checks every text colour for",
        "WCAG AA on the ground it is drawn on.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "#: Every ``--jv-*`` token the console declares, by name.",
        "TOKENS: dict[str, str] = {",
    ]
    for name, value, _path in web_tokens(tokens):
        lines.append(f"    {json.dumps(name)}: {json.dumps(value)},")
    lines.append("}")
    lines.append("")
    lines.append("# --- the palette, named for the desktop's jobs ---------------------------")
    for const, token_name in DESKTOP_COLOURS.items():
        lines.append(f'{const} = "{value_of(tokens, ("color", token_name))}"  # --jv-{token_name}')
    lines.append("")
    lines.append("__all__ = [\"TOKENS\", " + ", ".join(f'"{c}"' for c in DESKTOP_COLOURS) + "]")
    return "\n".join(lines) + "\n"


def value_of(tokens: dict, path: tuple[str, ...]) -> str:
    node = tokens
    for part in path:
        node = node[part]
    return str(resolve(tokens, node["$value"]))


def gen_kotlin_tokens(tokens: dict) -> str:
    lines = [
        "package ai.jarvis.app.ui.theme",
        "",
        "/**",
        f" * {MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        " *",
        " * The design tokens for the phone's Views. `JarvisUi` aliases these under the",
        " * names its builders use; `JarvisTheme` turns them into a Compose theme;",
        " * `android-app/tools/design_token_test.py` reads them back against the source.",
        " */",
        "object JarvisTokens {",
        "    object Color {",
    ]
    for path, kind, value in leaves(tokens["color"]):
        full = ("color",) + path
        css = css_name(full)
        lines.append(f"        const val {const_name(path)} = 0x{argb(str(resolve(tokens, value)))}.toInt() // {css}")
    for const, (token_name, alpha) in ANDROID_DERIVED.items():
        hexv = argb(value_of(tokens, ("color", token_name)), alpha)
        lines.append(f"        const val {const} = 0x{hexv}.toInt() // --jv-{token_name} at {round(alpha / 255 * 100)}%")
    lines.append("    }")
    lines.append("")
    lines.append("    /** The type scale in sp, named for the job. */")
    lines.append("    object Type {")
    for path, _kind, value in leaves(tokens["type"]["android"]):
        lines.append(f"        const val {const_name(path)} = {value}f")
    lines.append("    }")
    lines.append("")
    lines.append("    /** The spacing scale in dp, named for the job. */")
    lines.append("    object Space {")
    for path, _kind, value in leaves(tokens["space"]["android"]):
        lines.append(f"        const val {const_name(path)} = {value}")
    lines.append("    }")
    lines.append("")
    # Sizes, not spacing. A gap and a thing are different kinds of number, and
    # snapping the second to the first is how a 34 dp button quietly becomes a
    # 32 dp one — see the `size` block's description in tokens.json.
    lines.append("    /** Control and ornament sizes in dp, named for the job. */")
    lines.append("    object Size {")
    for path, _kind, value in leaves(tokens["size"]["android"]):
        lines.append(f"        const val {const_name(path)} = {value}")
    lines.append("    }")
    lines.append("")
    lines.append("    /** Corner radii in dp. */")
    lines.append("    object Radius {")
    for path, _kind, value in leaves(tokens["radius"]):
        px = str(value)
        if px.endswith("px") and px[:-2].isdigit():
            lines.append(f"        const val {const_name(path)} = {px[:-2]}")
    lines.append("    }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def gen_kotlin_theme(tokens: dict) -> str:
    return f"""package ai.jarvis.app.ui.theme

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
 * {MARK} — DO NOT EDIT. Run `python3 design/build.py`.
 *
 * The Compose theme, built from [JarvisTokens]. Reactor II is one dark world,
 * so there is one colour scheme and no light variant: a Composable that reads
 * `MaterialTheme.colorScheme` gets the same palette the console and the desktop
 * draw with.
 */
object JarvisColors {{
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
}}

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
fun JarvisTheme(content: @Composable () -> Unit) {{
    MaterialTheme(
        colorScheme = JarvisColorScheme,
        typography = JarvisTypography,
        shapes = JarvisShapes,
        content = content,
    )
}}
"""


def gen_xml_tokens(tokens: dict) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f"<!-- {MARK} — DO NOT EDIT. Run `python3 design/build.py`. -->",
        "<resources>",
    ]
    for path, _kind, value in leaves(tokens["color"]):
        name = "jv_" + "_".join(path).replace("-", "_")
        lines.append(f'    <color name="{name}">#{argb(str(resolve(tokens, value)))}</color>')
    for const, (token_name, alpha) in ANDROID_DERIVED.items():
        lines.append(f'    <color name="jv_{const.lower()}">#{argb(value_of(tokens, ("color", token_name)), alpha)}</color>')
    for path, _kind, value in leaves(tokens["space"]["android"]):
        lines.append(f'    <dimen name="jv_space_{path[-1]}">{value}dp</dimen>')
    for path, _kind, value in leaves(tokens["type"]["android"]):
        lines.append(f'    <dimen name="jv_type_{path[-1]}">{value}sp</dimen>')
    for path, _kind, value in leaves(tokens["radius"]):
        px = str(value)
        if px.endswith("px") and px[:-2].isdigit():
            lines.append(f'    <dimen name="jv_radius_{path[-1]}">{px[:-2]}dp</dimen>')
    lines.append("</resources>")
    return "\n".join(lines) + "\n"


def gen_xml_colors(tokens: dict) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f"<!-- {MARK} — DO NOT EDIT. Run `python3 design/build.py`.",
        "     The names the platform themes (themes.xml, values-v31) read, each an",
        "     alias of a token in tokens.xml. No hex lives here: a value typed in",
        "     this file is exactly the stale second palette this replaced. -->",
        "<resources>",
    ]
    for alias, target in XML_ALIASES.items():
        lines.append(f'    <color name="{alias}">@color/{target}</color>')
    lines.append("</resources>")
    return "\n".join(lines) + "\n"


# --- pinned files: checked, never rewritten -------------------------------------


def orb_expected(tokens: dict) -> dict[str, str]:
    out = {}
    for path, _kind, value in leaves(tokens["color"]["orb"]):
        out[".".join(path)] = str(value).lower()
    return out


def check_siri_palette(tokens: dict) -> list[str]:
    src = SIRI.read_text(encoding="utf-8")
    want = orb_expected(tokens)
    problems = []
    for tone in ("idle", "listening", "thinking", "speaking", "error"):
        arm = re.search(rf"Tone\.{tone.upper()} -> intArrayOf\((.*?)\)", src)
        core = re.search(rf"Tone\.{tone.upper()} -> 0x[0-9A-Fa-f]{{2}}([0-9A-Fa-f]{{6}})\.toInt\(\)", src)
        if not arm or not core:
            problems.append(f"SiriPalette.kt: no complete {tone} entry")
            continue
        blobs = [h.lower() for h in re.findall(r"0x[0-9A-Fa-f]{2}([0-9A-Fa-f]{6})", arm.group(1))]
        for i, hexv in enumerate(blobs):
            key = f"{tone}.blob-{i}"
            if want.get(key) != "#" + hexv:
                problems.append(f"SiriPalette.kt {tone} blob {i} is #{hexv}, tokens.json says {want.get(key)}")
        if want.get(f"{tone}.core") != "#" + core.group(1).lower():
            problems.append(f"SiriPalette.kt {tone} core is #{core.group(1).lower()}, tokens.json says {want.get(tone + '.core')}")
    return problems


def check_orb_shader(tokens: dict) -> list[str]:
    src = ORB.read_text(encoding="utf-8")
    want = orb_expected(tokens)
    problems = []
    for name, key in (("SUBSTRATE", "substrate"), ("HOUSING", "housing"), ("HUB_METAL", "hub-metal")):
        m = re.search(rf"const vec3 {name}\s*=\s*vec3\([^)]*\);\s*//\s*#([0-9A-Fa-f]{{6}})", src)
        if not m:
            problems.append(f"Orb.svelte: no hex comment beside {name}")
        elif want[key] != "#" + m.group(1).lower():
            problems.append(f"Orb.svelte {name} is #{m.group(1).lower()}, tokens.json says {want[key]}")
    for tone in ("idle", "listening", "thinking", "speaking"):
        m = re.search(rf"//\s*{tone}\s+#([0-9A-Fa-f]{{6}})\s+#([0-9A-Fa-f]{{6}})\s+#([0-9A-Fa-f]{{6}})\s*/\s*#([0-9A-Fa-f]{{6}})", src)
        if not m:
            problems.append(f"Orb.svelte: no palette comment for {tone}")
            continue
        for i, key in enumerate(("blob-0", "blob-1", "blob-2", "core")):
            got = "#" + m.group(i + 1).lower()
            if want[f"{tone}.{key}"] != got:
                problems.append(f"Orb.svelte {tone} {key} is {got}, tokens.json says {want[tone + '.' + key]}")
    return problems


# --- main ----------------------------------------------------------------------


def outputs(tokens: dict) -> dict[Path, str]:
    return {
        WEB_CSS: gen_css(tokens),
        WEB_TS: gen_ts(tokens),
        DESKTOP_PY: gen_py(tokens),
        KT_TOKENS: gen_kotlin_tokens(tokens),
        KT_THEME: gen_kotlin_theme(tokens),
        XML_TOKENS: gen_xml_tokens(tokens),
        XML_COLORS: gen_xml_colors(tokens),
    }


def main(argv: list[str]) -> int:
    tokens = load()
    generated = outputs(tokens)
    if "--check" in argv:
        problems = []
        for path, text in generated.items():
            if not path.is_file():
                problems.append(f"missing: {path.relative_to(ROOT)}")
            elif path.read_text(encoding="utf-8") != text:
                problems.append(f"stale: {path.relative_to(ROOT)} (run python3 design/build.py)")
        problems += check_siri_palette(tokens)
        problems += check_orb_shader(tokens)
        if problems:
            print("\n".join(problems))
            return 1
        print(f"{len(generated)} generated files current; SiriPalette.kt and Orb.svelte match color.orb.*")
        return 0
    for path, text in generated.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    for problem in check_siri_palette(tokens) + check_orb_shader(tokens):
        print(f"DRIFT: {problem}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
