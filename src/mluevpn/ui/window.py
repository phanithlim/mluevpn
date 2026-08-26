"""Main window: the list of VPNs and the connection log."""

from __future__ import annotations

import threading
import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import db  # noqa: E402
from .. import deps  # noqa: E402
from .. import APP_NAME  # noqa: E402
from ..backends import State  # noqa: E402
from ..db import Profile, Store  # noqa: E402
from ..manager import VpnManager  # noqa: E402
from . import theme  # noqa: E402
from .deps_dialog import DepsDialog  # noqa: E402
from .profile_dialog import ProfileDialog  # noqa: E402

# Short enough to sit in a badge without stretching the row.
STATE_LABEL = {
    State.DISCONNECTED: "Offline",
    State.CONNECTING: "Connecting",
    State.AUTHENTICATING: "Authenticating",
    State.CONNECTED: "Connected",
    State.DISCONNECTING: "Disconnecting",
    State.FAILED: "Failed",
}

# libadwaita ships these accent classes; reuse them so the badge follows the
# user's theme and accent colour instead of hard-coding our own.
STATE_CSS = {
    State.DISCONNECTED: "dimmed",
    State.CONNECTING: "accent",
    State.AUTHENTICATING: "accent",
    State.CONNECTED: "success",
    State.DISCONNECTING: "warning",
    State.FAILED: "error",
}
ALL_STATE_CSS = ("success", "error", "warning", "accent", "dimmed")

KIND_ICON = {
    db.KIND_OPENCONNECT: "network-vpn-symbolic",
    db.KIND_OPENVPN3: "network-workgroup-symbolic",
}

SETTING_THEME = "theme"
SETTING_LOG_VISIBLE = "log_visible"

MAX_LOG_LINES = 2000


def humanise(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class ProfileRow(Adw.ActionRow):
    """One VPN in the list: identity on the left, state and controls right."""

    def __init__(self, window: "MainWindow", profile: Profile) -> None:
        super().__init__(title=profile.name, subtitle=profile.target)
        self.window = window
        self.profile = profile
        self.connected_since: float | None = None

        self.set_subtitle_lines(1)
        self.set_tooltip_text(profile.target)
        # Clicking the row body opens the editor; the buttons take their own
        # clicks before this fires.
        self.set_activatable(True)
        self.connect("activated", lambda *_: window.edit_profile(self.profile))

        icon = Gtk.Image.new_from_icon_name(
            KIND_ICON.get(profile.kind, "network-vpn-symbolic")
        )
        icon.set_valign(Gtk.Align.CENTER)
        self.add_prefix(icon)

        self.duration = Gtk.Label(visible=False, valign=Gtk.Align.CENTER)
        self.duration.add_css_class("duration")
        self.add_suffix(self.duration)

        self.pill = Gtk.Label(valign=Gtk.Align.CENTER)
        self.pill.add_css_class("status-pill")
        self.add_suffix(self.pill)

        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER, visible=False)
        self.add_suffix(self.spinner)

        self.action_button = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER)
        self.action_button.connect("clicked", self._on_action)
        self.add_suffix(self.action_button)

        menu = Gio.Menu()
        menu.append("Edit…", f"win.edit({profile.id})")
        menu.append("Forget saved credentials", f"win.forget({profile.id})")
        menu.append("Delete", f"win.delete({profile.id})")
        self.add_suffix(
            Gtk.MenuButton(
                icon_name="view-more-symbolic",
                valign=Gtk.Align.CENTER,
                menu_model=menu,
                css_classes=["flat"],
                tooltip_text="More actions",
            )
        )

        self.refresh(State.DISCONNECTED, "")

    def _on_action(self, _button) -> None:
        if self.window.manager.state_of(self.profile.id).is_active:
            self.window.disconnect_profile(self.profile)
        else:
            self.window.connect_profile(self.profile)

    def refresh(self, state: State, detail: str) -> None:
        self.pill.set_label(STATE_LABEL[state])
        for css in ALL_STATE_CSS:
            self.pill.remove_css_class(css)
        self.pill.add_css_class(STATE_CSS[state])
        self.pill.set_tooltip_text(detail or None)

        if state is State.CONNECTED:
            if self.connected_since is None:
                self.connected_since = time.monotonic()
        else:
            self.connected_since = None
        self.duration.set_visible(state is State.CONNECTED)
        self.tick()

        self.spinner.set_visible(state.is_busy)
        self.spinner.set_spinning(state.is_busy)

        self.action_button.set_label("Disconnect" if state.is_active else "Connect")
        self.action_button.set_sensitive(not state.is_busy)
        for css in ("suggested-action", "destructive-action"):
            self.action_button.remove_css_class(css)
        self.action_button.add_css_class(
            "destructive-action" if state.is_active else "suggested-action"
        )

    def tick(self) -> None:
        """Advance the connected-for counter. Called once a second."""
        if self.connected_since is not None:
            self.duration.set_label(humanise(time.monotonic() - self.connected_since))


class MainWindow(Adw.ApplicationWindow):
    def __init__(
        self, application: Adw.Application, store: Store, manager: VpnManager
    ) -> None:
        super().__init__(application=application, title=APP_NAME)
        self.store = store
        self.manager = manager
        self.rows: dict[int, ProfileRow] = {}
        self._log_lines = 0

        self.set_default_size(700, 640)
        self.set_size_request(420, 400)
        self._build()
        self._install_actions(application)

        manager.on_state = self._on_state
        manager.on_log = self._on_log

        self.reload_profiles()
        self._refresh_banners()
        self.check_dependencies()
        GLib.timeout_add_seconds(1, self._tick)
        GLib.idle_add(self._autoconnect)

    # ----------------------------------------------------------------- layout

    def _build(self) -> None:
        self.window_title = Adw.WindowTitle(title=APP_NAME, subtitle="")
        header = Adw.HeaderBar(title_widget=self.window_title)

        add = Gtk.Button(
            icon_name="list-add-symbolic",
            tooltip_text="Add a VPN (Ctrl+N)",
            action_name="win.newvpn",
        )
        header.pack_start(add)

        self.log_toggle = Gtk.ToggleButton(
            icon_name="utilities-terminal-symbolic",
            tooltip_text="Show the connection log (Ctrl+L)",
        )
        self.log_toggle.connect("toggled", self._on_log_toggled)
        header.pack_end(
            Gtk.MenuButton(
                icon_name="open-menu-symbolic",
                tooltip_text="Main menu",
                menu_model=self._main_menu(),
            )
        )
        header.pack_end(self.log_toggle)

        # -- banners ---------------------------------------------------------
        self.sudo_banner = Adw.Banner(
            title="OpenConnect needs your sudo password to run",
            button_label="Set password",
            revealed=False,
        )
        self.sudo_banner.connect("button-clicked", lambda *_: self._on_sudo())

        self.key_banner = Adw.Banner(
            title="No keyring available — the encryption key is in a local file",
            revealed=False,
        )

        # Filled in by the background probe started at the end of __init__.
        self.deps_banner = Adw.Banner(
            button_label="System check",
            revealed=False,
        )
        self.deps_banner.connect("button-clicked", lambda *_: self._on_deps())

        # -- profile list ----------------------------------------------------
        self.group = Adw.PreferencesGroup(title="VPNs")
        page = Adw.PreferencesPage()
        page.add(self.group)

        self.empty = Adw.StatusPage(
            icon_name="network-vpn-symbolic",
            title="No VPNs yet",
            description="Add an OpenConnect server or an OpenVPN .ovpn file "
                        "to get started.",
            vexpand=True,
        )
        add_first = Gtk.Button(
            label="Add a VPN",
            halign=Gtk.Align.CENTER,
            css_classes=["suggested-action", "pill"],
            action_name="win.newvpn",
        )
        self.empty.set_child(add_first)

        self.list_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE
        )
        self.list_stack.add_named(page, "list")
        self.list_stack.add_named(self.empty, "empty")

        # -- log -------------------------------------------------------------
        self.log_box = self._build_log()

        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_start_child(self.list_stack)
        paned.set_end_child(self.log_box)
        # Extra window height goes to the VPN list; the log keeps whatever
        # height the user drags it to rather than growing with the window.
        paned.set_resize_start_child(True)
        paned.set_resize_end_child(False)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self.deps_banner)
        view.add_top_bar(self.sudo_banner)
        view.add_top_bar(self.key_banner)
        view.set_content(paned)

        self.toasts = Adw.ToastOverlay(child=view)
        self.set_content(self.toasts)

    def _build_log(self) -> Gtk.Box:
        self.log_buffer = Gtk.TextBuffer()
        log_view = Gtk.TextView(
            buffer=self.log_buffer,
            editable=False,
            cursor_visible=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            left_margin=12, right_margin=12, top_margin=8, bottom_margin=8,
            css_classes=["log-view"],
        )
        self.log_scroll = Gtk.ScrolledWindow(
            child=log_view, vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )

        self.log_placeholder = Adw.StatusPage(
            icon_name="utilities-terminal-symbolic",
            description="Connection output will appear here.",
            vexpand=True,
        )
        self.log_stack = Gtk.Stack(vexpand=True)
        self.log_stack.add_named(self.log_placeholder, "empty")
        self.log_stack.add_named(self.log_scroll, "log")

        title = Gtk.Label(label="Connection log", xalign=0, hexpand=True)
        title.add_css_class("heading")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        head.add_css_class("log-header")
        head.append(title)
        for icon, tip, action in (
            ("edit-copy-symbolic", "Copy the log", "win.copylog"),
            ("user-trash-symbolic", "Clear the log", "win.clearlog"),
            ("window-close-symbolic", "Hide the log", "win.hidelog"),
        ):
            head.append(
                Gtk.Button(
                    icon_name=icon, tooltip_text=tip,
                    action_name=action, css_classes=["flat"],
                    valign=Gtk.Align.CENTER,
                )
            )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        box.append(Gtk.Separator())
        box.append(head)
        box.append(Gtk.Separator())
        box.append(self.log_stack)
        box.set_size_request(-1, 200)
        return box

    def _main_menu(self) -> Gio.Menu:
        appearance = Gio.Menu()
        for key, label in theme.THEMES:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.theme", GLib.Variant("s", key))
            appearance.append_item(item)

        actions = Gio.Menu()
        actions.append("System check…", "win.deps")
        actions.append("Sudo password…", "win.sudo")
        actions.append("Show connection log", "win.togglelog")

        menu = Gio.Menu()
        menu.append_section(None, actions)
        menu.append_submenu("Appearance", appearance)

        about = Gio.Menu()
        about.append(f"About {APP_NAME}", "win.about")
        menu.append_section(None, about)
        return menu

    # ---------------------------------------------------------------- actions

    def _install_actions(self, application: Adw.Application) -> None:
        simple: dict[str, Callable[[], None]] = {
            "newvpn": lambda: self.edit_profile(None),
            "sudo": self._on_sudo,
            "deps": self._on_deps,
            "clearlog": self._clear_log,
            "copylog": self._copy_log,
            "hidelog": lambda: self.log_toggle.set_active(False),
            "togglelog": lambda: self.log_toggle.set_active(
                not self.log_toggle.get_active()
            ),
            "about": self._on_about,
        }
        for name, handler in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: h())
            self.add_action(action)

        targeted: dict[str, Callable[[int], None]] = {
            "edit": lambda pid: self.edit_profile(self.store.get_profile(pid)),
            "delete": self._on_delete,
            "forget": self._on_forget,
        }
        for name, handler in targeted.items():
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("i"))
            action.connect("activate", lambda _a, p, h=handler: h(p.unpack()))
            self.add_action(action)

        # -- theme (stateful radio group) ------------------------------------
        current = self.store.get_setting(SETTING_THEME, theme.SYSTEM)
        theme.apply(current)
        theme_action = Gio.SimpleAction.new_stateful(
            "theme", GLib.VariantType.new("s"), GLib.Variant("s", current)
        )
        theme_action.connect("activate", self._on_theme)
        self.add_action(theme_action)

        for action_name, accels in (
            ("win.newvpn", ["<Primary>n"]),
            ("win.togglelog", ["<Primary>l"]),
            ("app.quit", ["<Primary>q", "<Primary>w"]),
        ):
            application.set_accels_for_action(action_name, accels)

        # Restore the log pane the way the user left it.
        if self.store.get_setting(SETTING_LOG_VISIBLE, "0") == "1":
            self.log_toggle.set_active(True)

    def _on_theme(self, action: Gio.SimpleAction, target: GLib.Variant) -> None:
        name = target.unpack()
        action.set_state(target)
        theme.apply(name)
        self.store.set_setting(SETTING_THEME, name)

    # --------------------------------------------------------------- profiles

    def reload_profiles(self) -> None:
        for row in self.rows.values():
            self.group.remove(row)
        self.rows.clear()

        profiles = self.store.list_profiles()
        for profile in profiles:
            row = ProfileRow(self, profile)
            row.refresh(self.manager.state_of(profile.id), "")
            self.group.add(row)
            self.rows[profile.id] = row

        self.list_stack.set_visible_child_name("list" if profiles else "empty")
        self._refresh_summary()
        self._refresh_banners()

    def _refresh_summary(self) -> None:
        total = len(self.rows)
        if not total:
            self.window_title.set_subtitle("")
            return
        live = sum(
            1 for pid in self.rows if self.manager.state_of(pid) is State.CONNECTED
        )
        plural = "" if total == 1 else "s"
        self.window_title.set_subtitle(
            f"{total} VPN{plural} · {live} connected" if live
            else f"{total} VPN{plural}"
        )

    def _refresh_banners(self) -> None:
        needs_sudo = any(
            p.kind == db.KIND_OPENCONNECT for p in self.store.list_profiles()
        )
        self.sudo_banner.set_revealed(
            needs_sudo and not self.store.has_app_secret(db.SUDO_PASSWORD)
        )
        self.key_banner.set_revealed(not self.store.box.source.is_secure)

    # ----------------------------------------------------------- dependencies

    def check_dependencies(self) -> None:
        """Probe openconnect/openvpn3 in the background and update the banner.

        `pacman -Si` can take a moment, and this runs while the window is being
        shown, so it must not touch the main loop until it has an answer.
        """
        threading.Thread(target=self._probe_dependencies, daemon=True).start()

    def _probe_dependencies(self) -> None:
        try:
            statuses = deps.check_all()
        except Exception as exc:  # noqa: BLE001 - a worker thread must not die
            log_line = f"dependency check failed: {exc}"
            GLib.idle_add(self.append_log, log_line)
            return
        GLib.idle_add(self._show_dependency_banner, statuses)

    def _show_dependency_banner(self, statuses: list[deps.DepStatus]) -> bool:
        missing = deps.missing_required(statuses)
        outdated = [s for s in statuses if s.state == deps.OUTDATED]

        if missing:
            names = ", ".join(s.dependency.name for s in missing)
            plural = "are" if len(missing) > 1 else "is"
            self.deps_banner.set_title(
                f"{names} {plural} not installed — connecting will fail"
            )
        elif outdated:
            names = ", ".join(s.dependency.name for s in outdated)
            self.deps_banner.set_title(f"Update available for {names}")
        self.deps_banner.set_revealed(bool(missing or outdated))
        return GLib.SOURCE_REMOVE

    def _on_deps(self) -> None:
        dialog = DepsDialog(self.store.box.source)
        # Whatever the user installed while the page was open should be
        # reflected in the banner once they close it.
        dialog.connect("closed", lambda *_: self.check_dependencies())
        dialog.present(self)

    def edit_profile(self, profile: Profile | None) -> None:
        def saved(_saved: Profile) -> None:
            self.reload_profiles()
            self.toast("Profile saved")

        ProfileDialog(self.store, profile, saved).present(self)

    def connect_profile(self, profile: Profile) -> None:
        if profile.kind == db.KIND_OPENCONNECT and not self.store.has_app_secret(
            db.SUDO_PASSWORD
        ):
            self._on_sudo(
                reason="OpenConnect has to run as root. Save your sudo password "
                       "once and myvpn will answer the prompt for you.",
                then=lambda: self.connect_profile(profile),
            )
            return

        self.append_log(f"--- connecting {profile.name} ---")
        try:
            self.manager.connect(profile)
        except ValueError as exc:
            self.toast(str(exc))
            self.append_log(f"error: {exc}")

    def disconnect_profile(self, profile: Profile) -> None:
        self.append_log(f"--- disconnecting {profile.name} ---")
        self.manager.disconnect(profile)

    def _on_delete(self, profile_id: int) -> None:
        profile = self.store.get_profile(profile_id)
        if profile is None:
            return

        alert = Adw.AlertDialog(
            heading=f"Delete {profile.name}?",
            body="The profile and its saved credentials will be removed. "
                 "This cannot be undone.",
        )
        alert.add_response("cancel", "Cancel")
        alert.add_response("delete", "Delete")
        alert.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_default_response("cancel")
        alert.set_close_response("cancel")

        def responded(_dialog, response: str) -> None:
            if response != "delete":
                return
            if self.manager.state_of(profile_id).is_active:
                self.manager.disconnect(profile)
            self.manager.forget(profile_id)
            self.store.delete_profile(profile_id)
            self.reload_profiles()
            self.toast(f"Deleted {profile.name}")

        alert.connect("response", responded)
        alert.present(self)

    def _on_forget(self, profile_id: int) -> None:
        self.store.clear_secrets(profile_id)
        self.toast("Saved credentials cleared for this profile")

    # --------------------------------------------------------------- settings

    def _on_sudo(
        self, reason: str | None = None, then: Callable[[], None] | None = None
    ) -> None:
        stored = self.store.has_app_secret(db.SUDO_PASSWORD)
        alert = Adw.AlertDialog(
            heading="Sudo password",
            body=reason or (
                "Used to answer the [sudo] prompt when starting OpenConnect. "
                "Stored encrypted in the local database."
            ),
        )

        entry = Adw.PasswordEntryRow(title="Password")
        group = Adw.PreferencesGroup()
        group.add(entry)
        alert.set_extra_child(group)

        alert.add_response("cancel", "Cancel")
        if stored:
            alert.add_response("clear", "Remove stored")
            alert.set_response_appearance(
                "clear", Adw.ResponseAppearance.DESTRUCTIVE
            )
        alert.add_response("save", "Save")
        alert.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("save")
        alert.set_close_response("cancel")

        def responded(_dialog, response: str) -> None:
            if response == "clear":
                self.store.set_app_secret(db.SUDO_PASSWORD, None)
                self._refresh_banners()
                self.toast("Stored sudo password removed")
                return
            if response != "save":
                return
            value = entry.get_text()
            if not value:
                self.toast("No password entered")
                return
            self.store.set_app_secret(db.SUDO_PASSWORD, value)
            self._refresh_banners()
            self.toast("Sudo password saved")
            if then:
                then()

        alert.connect("response", responded)
        # Enter in the password field should save rather than do nothing.
        entry.connect("entry-activated", lambda *_: alert.response("save"))
        alert.present(self)

    def _on_about(self) -> None:
        from .. import DESCRIPTION, __version__

        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon="network-vpn-symbolic",
            version=__version__,
            comments=DESCRIPTION,
            license_type=Gtk.License.MIT_X11,
        )
        about.add_credit_section("Backends", ["openconnect", "openvpn3"])
        about.present(self)

    # ------------------------------------------------------------- callbacks

    def _on_state(self, profile_id: int, state: State, detail: str) -> None:
        row = self.rows.get(profile_id)
        if row is not None:
            row.refresh(state, detail)
        self._refresh_summary()

        name = row.profile.name if row else "VPN"
        if state is State.CONNECTED:
            self.toast(f"{name} connected")
        elif state is State.FAILED:
            self.toast(detail or f"{name} failed to connect")
            # A failure is exactly when the log stops being optional.
            self.log_toggle.set_active(True)

    def _on_log(self, profile_id: int, line: str) -> None:
        row = self.rows.get(profile_id)
        prefix = f"{row.profile.name}: " if row else ""
        self.append_log(f"{prefix}{line}")

    def _tick(self) -> bool:
        for row in self.rows.values():
            row.tick()
        return GLib.SOURCE_CONTINUE

    # -------------------------------------------------------------- log/toast

    def _on_log_toggled(self, button: Gtk.ToggleButton) -> None:
        shown = button.get_active()
        self.log_box.set_visible(shown)
        button.set_tooltip_text(
            "Hide the connection log (Ctrl+L)" if shown
            else "Show the connection log (Ctrl+L)"
        )
        self.store.set_setting(SETTING_LOG_VISIBLE, "1" if shown else "0")

    def append_log(self, line: str) -> None:
        buf = self.log_buffer
        buf.insert(buf.get_end_iter(), f"{time.strftime('%H:%M:%S')}  {line}\n")
        self._log_lines += 1

        # Trim from the front so a chatty tunnel cannot grow the buffer forever.
        if buf.get_line_count() > MAX_LOG_LINES:
            excess = buf.get_line_count() - MAX_LOG_LINES
            buf.delete(buf.get_start_iter(), buf.get_iter_at_line(excess)[1])

        self.log_stack.set_visible_child_name("log")

        adj = self.log_scroll.get_vadjustment()
        # Only follow the tail if the user has not scrolled up to read history.
        at_bottom = adj.get_value() >= adj.get_upper() - adj.get_page_size() - 60
        if at_bottom:
            GLib.idle_add(
                lambda: adj.set_value(adj.get_upper()) or GLib.SOURCE_REMOVE
            )

    def _clear_log(self) -> None:
        self.log_buffer.set_text("")
        self._log_lines = 0
        self.log_stack.set_visible_child_name("empty")

    def _copy_log(self) -> None:
        text = self.log_buffer.get_text(
            self.log_buffer.get_start_iter(), self.log_buffer.get_end_iter(), False
        )
        if not text.strip():
            self.toast("The log is empty")
            return
        self.get_clipboard().set(text)
        self.toast("Log copied")

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message, timeout=4))

    # -------------------------------------------------------------- autostart

    def _autoconnect(self) -> bool:
        for profile in self.store.list_profiles():
            if profile.autoconnect:
                self.connect_profile(profile)
        return GLib.SOURCE_REMOVE
