"""Jarvis's look, for the two dialogs this agent draws.

The browser console, the Android app and this agent are three surfaces of one
assistant, and the same person sees at least two of them within a minute of each
other — a question on the phone, an approval on the desktop. Until this module
existed the desktop's two dialogs did not even match *each other*: the consent
prompt was grey system chrome with ``TkDefaultFont`` bold, the companion
question was grey system chrome with ``TkFixedFont``, and neither shared a
single value with the HUD they are both named after.

``jarvis-web/src/lib/tokens.ts`` is the palette and stays the palette. Every
colour below is a ``--jv-*`` token copied from it with the token named beside
it, exactly as ``android-app/.../ui/JarvisUi.kt`` does for the phone, and
``tests/test_theme.py`` reads that file and diffs it against this one so the
three surfaces cannot drift. That test also checks every text colour for WCAG AA
against the ground it is actually drawn on, which is not decoration: the same
check found ``FAINT`` on Android sitting at 4.38:1 — under AA — and it was the
colour every hint on every screen was written in.

Two things are deliberately *not* mirrored:

* **The type scale.** The tokens are ``rem``; Tk wants points, and a point is
  not a fraction of a root font size. The sizes below are chosen to look like
  the console's chrome, and nothing pins them, because a pin that has to be
  hand-converted is a pin that will be wrong.
* **tkinter itself.** It is imported inside each helper rather than at the top,
  because half the reason this agent has a terminal fallback at all is that
  ``python3-tk`` is missing on plenty of the machines it runs on. Importing it
  here would turn a missing optional toolkit into a failure to start.

On macOS, Aqua buttons ignore ``bg``/``fg`` outright, so there the dialogs get
the dark ground and the accent chrome but keep native buttons. Fixing that means
``ttk`` styles, which cannot draw a filled button on every platform either —
this is the version that degrades rather than the version that looks right on
one machine.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "BG",
    "PANEL",
    "ACCENT",
    "ACCENT_DEEP",
    "ACCENT_INK",
    "TEXT",
    "TEXT_BRIGHT",
    "TEXT_DIM",
    "TEXT_FAINT",
    "OK",
    "DANGER",
    "WARN",
    "MONO",
    "FS_HEADER",
    "FS_BODY",
    "FS_SMALL",
    "BUTTON_KINDS",
    "style_window",
    "wordmark",
    "label",
    "readout",
    "message",
    "row",
    "field",
    "button",
]

# --- the palette ------------------------------------------------------------
#
# Every colour is a --jv-* token from design/tokens.json, generated into
# ``tokens.py`` by ``design/build.py`` (which also names the token beside each
# value). Nothing here may hold a hex of its own: a colour with no token is the
# thing this module exists to prevent — a fourth palette, growing back one
# constant at a time. ``tests/test_theme.py`` checks the generated lines for AA.
from .tokens import (  # noqa: F401 — re-exported under the names the dialogs use
    ACCENT,
    ACCENT_DEEP,
    ACCENT_INK,
    BG,
    DANGER,
    OK,
    PANEL,
    TEXT,
    TEXT_BRIGHT,
    TEXT_DIM,
    TEXT_FAINT,
    WARN,
)

# --- chrome -----------------------------------------------------------------

#: ``--jv-font-chrome`` is a monospace stack. ``TkFixedFont`` is the portable
#: way to say the same thing — "this machine's fixed-width font" — and it is
#: the only spelling that resolves on Linux, macOS and Windows alike.
MONO = "TkFixedFont"

FS_HEADER = 12
FS_BODY = 10
FS_SMALL = 9

#: ``kind -> (fill, ink)``. Buttons are filled rather than outlined because the
#: two that matter are answers to a question about running something on this
#: machine, and an outline reads as decoration.
BUTTON_KINDS = {
    "approve": (OK, ACCENT_INK),
    "deny": (DANGER, ACCENT_INK),
    "accent": (ACCENT, ACCENT_INK),
    #: For everything that is not an answer: Dismiss, Always allow.
    "quiet": (PANEL, TEXT),
}


# --- widgets ----------------------------------------------------------------


def style_window(root: Any, title: str) -> None:
    """Dark ground, a title, and on top of whatever the user is doing.

    ``-topmost`` and the dark ground are both best effort: a window manager may
    refuse the first, and a Tk build with no colour support would raise on the
    second. Neither is worth failing a consent prompt over — a prompt that
    opens looking wrong is still a prompt, a prompt that raises is a denial the
    user never saw.
    """
    try:
        root.title(title)
    except Exception:  # noqa: BLE001
        pass
    try:
        root.configure(bg=BG)
    except Exception:  # noqa: BLE001
        pass
    try:
        root.attributes("-topmost", True)
    except Exception:  # noqa: BLE001
        pass


def wordmark(parent: Any, text: str = "JARVIS") -> Any:
    """The line both dialogs open with, so both are recognisable at a glance."""
    import tkinter as tk  # local: see the module docstring

    return tk.Label(
        parent,
        text=text,
        font=(MONO, FS_HEADER, "bold"),
        fg=ACCENT,
        bg=BG,
        anchor="w",
        justify="left",
    )


def label(
    parent: Any,
    text: str,
    colour: str = TEXT,
    size: int = FS_BODY,
    weight: str = "normal",
) -> Any:
    """One line of chrome on the ground."""
    import tkinter as tk

    return tk.Label(
        parent,
        text=text,
        font=(MONO, size, weight),
        fg=colour,
        bg=BG,
        anchor="w",
        justify="left",
    )


def readout(parent: Any, text: str, height: int = 16, width: int = 76) -> Any:
    """The read-only slab the verbatim prompt is printed into.

    Disabled after the insert, not merely non-editable by convention: the text
    in here is the truth about what is going to run, and a widget the user can
    type into is a widget that can be made to disagree with the request.
    """
    import tkinter as tk

    widget = tk.Text(
        parent,
        height=height,
        width=width,
        wrap="word",
        font=(MONO, FS_BODY),
        bg=PANEL,
        fg=TEXT,
        insertbackground=ACCENT,
        relief="flat",
        highlightthickness=1,
        highlightbackground=ACCENT_DEEP,
        highlightcolor=ACCENT,
        padx=10,
        pady=8,
    )
    widget.insert("1.0", text)
    widget.configure(state="disabled")
    return widget


def message(parent: Any, text: str, width: int = 520) -> Any:
    """A wrapping block of server-supplied text. Rendered, never interpreted."""
    import tkinter as tk

    return tk.Message(
        parent,
        text=text,
        width=width,
        font=(MONO, FS_BODY),
        fg=TEXT_BRIGHT,
        bg=BG,
        anchor="w",
        justify="left",
    )


def row(parent: Any) -> Any:
    """A frame for a row of buttons. Exists so the frame does not show up as a
    grey band across the ground."""
    import tkinter as tk

    return tk.Frame(parent, bg=BG)


def field(parent: Any) -> Any:
    """The typed-answer box."""
    import tkinter as tk

    return tk.Entry(
        parent,
        font=(MONO, FS_BODY),
        bg=PANEL,
        fg=TEXT_BRIGHT,
        insertbackground=ACCENT,
        relief="flat",
        highlightthickness=1,
        highlightbackground=ACCENT_DEEP,
        highlightcolor=ACCENT,
    )


def button(parent: Any, text: str, command: Any, kind: str = "quiet", width: int = 12) -> Any:
    """One answer. ``kind`` picks the fill from :data:`BUTTON_KINDS`."""
    import tkinter as tk

    fill, ink = BUTTON_KINDS.get(kind, BUTTON_KINDS["quiet"])
    return tk.Button(
        parent,
        text=text,
        command=command,
        width=width,
        font=(MONO, FS_BODY, "bold"),
        bg=fill,
        fg=ink,
        activebackground=fill,
        activeforeground=ink,
        highlightbackground=BG,
        relief="flat",
        borderwidth=0,
        padx=8,
        pady=4,
    )
