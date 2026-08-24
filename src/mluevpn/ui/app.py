"""Application object and shutdown handling."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID, APP_NAME  # noqa: E402
from ..config import LOG_PATH, ensure_dirs  # noqa: E402
from ..crypto import SecretsUnavailable  # noqa: E402
from ..db import Store  # noqa: E402
from ..manager import VpnManager  # noqa: E402
from .window import MainWindow  # noqa: E402

log = logging.getLogger(__name__)


class MyVpnApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.store: Store | None = None
        self.manager: VpnManager | None = None
        self.window: MainWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self._load_css()
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self._request_quit())
        self.add_action(quit_action)

        # Ctrl-C in the launching terminal should shut down as cleanly as
        # closing the window does.
        GLib.unix_signal_add(
            GLib.PRIORITY_DEFAULT, 2, lambda: self._request_quit() or True
        )

    def _load_css(self) -> None:
        """Load style.css from beside this module, whether installed or not."""
        css = Path(__file__).with_name("style.css")
        if not css.is_file():
            log.warning("stylesheet missing at %s", css)
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(str(css))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
            return

        try:
            self.store = Store()
        except SecretsUnavailable as exc:
            self._fatal(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._fatal(f"Could not open the database: {exc}")
            return

        self.manager = VpnManager(self.store)
        self.window = MainWindow(self, self.store, self.manager)
        self.window.connect("close-request", self._on_close_request)
        self.window.present()

    # ---------------------------------------------------------------- closing

    def _on_close_request(self, window) -> bool:
        """Never silently leave a tunnel up after the window disappears."""
        if self.manager is None or not self.manager.any_active():
            self._shutdown()
            return False

        active = [
            self.store.get_profile(pid) for pid in self.manager.active_profile_ids()
        ]
        names = ", ".join(p.name for p in active if p)

        alert = Adw.AlertDialog(
            heading="Disconnect before quitting?",
            body=f"Still connected: {names}. Quitting without disconnecting "
                 f"would leave the tunnel up in the background.",
        )
        alert.add_response("cancel", "Stay open")
        alert.add_response("leave", "Quit anyway")
        alert.add_response("disconnect", "Disconnect and quit")
        alert.set_response_appearance(
            "disconnect", Adw.ResponseAppearance.SUGGESTED
        )
        alert.set_response_appearance("leave", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_default_response("disconnect")
        alert.set_close_response("cancel")

        def responded(_dialog, response):
            if response == "cancel":
                return
            if response == "disconnect":
                window.append_log("disconnecting everything before quitting...")
                self.manager.disconnect_all(wait=25)
            self._shutdown()
            window.destroy()
            self.quit()

        alert.connect("response", responded)
        alert.present(window)
        return True  # hold the window open until the dialog is answered

    def _request_quit(self) -> None:
        if self.window is not None:
            self.window.close()
        else:
            self._shutdown()
            self.quit()

    def _shutdown(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None

    def _fatal(self, message: str) -> None:
        dialog = Adw.AlertDialog(heading=f"{APP_NAME} cannot start", body=message)
        dialog.add_response("quit", "Quit")
        dialog.connect("response", lambda *_: self.quit())
        window = Adw.ApplicationWindow(application=self, title=APP_NAME)
        window.set_default_size(420, 200)
        window.present()
        dialog.present(window)


def run(argv: list[str] | None = None) -> int:
    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    return MyVpnApp().run(argv if argv is not None else sys.argv)
