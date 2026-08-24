"""Light/dark/system switching that survives Omarchy's GTK theming.

Omarchy themes GTK by writing `~/.config/gtk-4.0/gtk.css` with a block of
`@define-color` overrides (window_bg_color, view_bg_color, accent_color, ...).
GTK loads that file at GTK_STYLE_PROVIDER_PRIORITY_USER, which outranks
libadwaita's own stylesheet -- so asking libadwaita for the light scheme sets
the scheme correctly but leaves every colour pinned to the desktop theme's
values, and the window still looks dark.

There is no way to un-define a `@define-color`, so forcing a scheme means
supplying the whole palette ourselves at a priority above USER. "Follow system"
installs nothing and lets Omarchy's colours through, which is what following
the system should mean on this desktop.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

SYSTEM = "system"
LIGHT = "light"
DARK = "dark"

THEMES = [
    (SYSTEM, "Follow system"),
    (LIGHT, "Light"),
    (DARK, "Dark"),
]

_SCHEMES = {
    SYSTEM: Adw.ColorScheme.DEFAULT,
    LIGHT: Adw.ColorScheme.FORCE_LIGHT,
    DARK: Adw.ColorScheme.FORCE_DARK,
}

# libadwaita's stock palettes. Only the tokens a desktop theme is likely to
# override need to be here, plus the status colours the status pills rely on.
_LIGHT_PALETTE = """
@define-color accent_color #1c71d8;
@define-color accent_bg_color #3584e4;
@define-color accent_fg_color #ffffff;
@define-color destructive_color #c01c28;
@define-color destructive_bg_color #e01b24;
@define-color destructive_fg_color #ffffff;
@define-color success_color #1b8553;
@define-color success_bg_color #2ec27e;
@define-color success_fg_color #ffffff;
@define-color warning_color #9c6e03;
@define-color warning_bg_color #f5c211;
@define-color warning_fg_color rgba(0, 0, 0, 0.8);
@define-color error_color #c01c28;
@define-color error_bg_color #e01b24;
@define-color error_fg_color #ffffff;
@define-color window_bg_color #fafafb;
@define-color window_fg_color #17171b;
@define-color view_bg_color #ffffff;
@define-color view_fg_color #17171b;
@define-color headerbar_bg_color #ffffff;
@define-color headerbar_fg_color #17171b;
@define-color headerbar_border_color #17171b;
@define-color headerbar_backdrop_color #fafafb;
@define-color headerbar_shade_color rgba(0, 0, 6, 0.12);
@define-color popover_bg_color #ffffff;
@define-color popover_fg_color #17171b;
@define-color card_bg_color #ffffff;
@define-color card_fg_color #17171b;
@define-color dialog_bg_color #fafafb;
@define-color dialog_fg_color #17171b;
@define-color sidebar_bg_color #ebebed;
@define-color sidebar_fg_color #17171b;
@define-color sidebar_border_color rgba(0, 0, 6, 0.07);
@define-color sidebar_backdrop_color #f2f2f4;
@define-color theme_selected_bg_color #3584e4;
@define-color theme_selected_fg_color #ffffff;
"""

_DARK_PALETTE = """
@define-color accent_color #78aeed;
@define-color accent_bg_color #3584e4;
@define-color accent_fg_color #ffffff;
@define-color destructive_color #ff7b63;
@define-color destructive_bg_color #c01c28;
@define-color destructive_fg_color #ffffff;
@define-color success_color #78e9ab;
@define-color success_bg_color #26a269;
@define-color success_fg_color #ffffff;
@define-color warning_color #f8e45c;
@define-color warning_bg_color #cd9309;
@define-color warning_fg_color rgba(0, 0, 0, 0.8);
@define-color error_color #ff938c;
@define-color error_bg_color #c01c28;
@define-color error_fg_color #ffffff;
@define-color window_bg_color #222226;
@define-color window_fg_color #ffffff;
@define-color view_bg_color #1d1d20;
@define-color view_fg_color #ffffff;
@define-color headerbar_bg_color #2e2e32;
@define-color headerbar_fg_color #ffffff;
@define-color headerbar_border_color #ffffff;
@define-color headerbar_backdrop_color #222226;
@define-color headerbar_shade_color rgba(0, 0, 6, 0.36);
@define-color popover_bg_color #36363a;
@define-color popover_fg_color #ffffff;
@define-color card_bg_color rgba(255, 255, 255, 0.08);
@define-color card_fg_color #ffffff;
@define-color dialog_bg_color #36363a;
@define-color dialog_fg_color #ffffff;
@define-color sidebar_bg_color #2e2e32;
@define-color sidebar_fg_color #ffffff;
@define-color sidebar_border_color rgba(0, 0, 6, 0.36);
@define-color sidebar_backdrop_color #28282c;
@define-color theme_selected_bg_color #3584e4;
@define-color theme_selected_fg_color #ffffff;
"""

_PALETTES = {LIGHT: _LIGHT_PALETTE, DARK: _DARK_PALETTE}

# One step above the priority the desktop's own gtk.css is loaded at, so our
# palette wins when -- and only when -- the user has forced a scheme.
_PRIORITY = Gtk.STYLE_PROVIDER_PRIORITY_USER + 1

_provider: Gtk.CssProvider | None = None


def apply(name: str) -> None:
    """Switch to 'system', 'light', or 'dark'."""
    global _provider

    if name not in _SCHEMES:
        name = SYSTEM
    Adw.StyleManager.get_default().set_color_scheme(_SCHEMES[name])

    display = Gdk.Display.get_default()
    if display is None:
        return

    if _provider is not None:
        Gtk.StyleContext.remove_provider_for_display(display, _provider)
        _provider = None

    palette = _PALETTES.get(name)
    if palette is None:
        # "Follow system": leave the desktop's own colours alone.
        return

    _provider = Gtk.CssProvider()
    _provider.load_from_string(palette)
    Gtk.StyleContext.add_provider_for_display(display, _provider, _PRIORITY)
