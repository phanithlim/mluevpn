"""Add/edit dialog for a single VPN profile."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from .. import db  # noqa: E402
from .. import APP_NAME  # noqa: E402
from ..crypto import SecretsUnavailable  # noqa: E402
from ..db import Profile, Store  # noqa: E402

KINDS = [
    (db.KIND_OPENCONNECT, "OpenConnect", "Cisco, GlobalProtect, Fortinet, ... (needs sudo)"),
    (db.KIND_OPENVPN3, "OpenVPN 3", "An .ovpn config file (runs as your user)"),
]

PROTOCOLS = [
    ("gp", "GlobalProtect (Palo Alto)"),
    ("anyconnect", "AnyConnect / ocserv (Cisco)"),
    ("fortinet", "FortiGate"),
    ("pulse", "Pulse Connect Secure"),
    ("nc", "Juniper Network Connect"),
    ("f5", "F5 BIG-IP"),
    ("array", "Array Networks"),
]


class ProfileDialog(Adw.Dialog):
    """Edits a Profile plus its stored password, in place."""

    def __init__(
        self,
        store: Store,
        profile: Profile | None,
        on_saved: Callable[[Profile], None],
    ) -> None:
        super().__init__()
        self.store = store
        self.profile = profile or Profile()
        self.on_saved = on_saved
        self._is_new = self.profile.id is None
        self._config_path: str = ""

        self.set_title("New VPN" if self._is_new else "Edit VPN")
        self.set_content_width(520)
        self.set_content_height(680)

        self._build()
        self._load()
        self._sync_kind_visibility()

    # ----------------------------------------------------------------- layout

    def _build(self) -> None:
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)

        self.save_button = Gtk.Button(label="Save", css_classes=["suggested-action"])
        self.save_button.connect("clicked", self._on_save)
        header.pack_end(self.save_button)

        page = Adw.PreferencesPage()

        # -- general ---------------------------------------------------------
        general = Adw.PreferencesGroup(title="General")
        self.name_row = Adw.EntryRow(title="Name")
        self.name_row.connect("changed", lambda *_: self._validate())
        general.add(self.name_row)

        self.kind_row = Adw.ComboRow(
            title="Type",
            model=Gtk.StringList.new([label for _, label, _ in KINDS]),
        )
        self.kind_row.connect("notify::selected", lambda *_: self._sync_kind_visibility())
        general.add(self.kind_row)
        page.add(general)

        # -- openconnect -----------------------------------------------------
        self.oc_group = Adw.PreferencesGroup(
            title="OpenConnect server",
            description="Run as root via sudo. The sudo password is set once, "
                        "in the main window's menu.",
        )
        self.host_row = Adw.EntryRow(title="Host or URL")
        self.host_row.connect("changed", lambda *_: self._validate())
        self.oc_group.add(self.host_row)

        self.protocol_row = Adw.ComboRow(
            title="Protocol",
            model=Gtk.StringList.new([label for _, label in PROTOCOLS]),
        )
        self.oc_group.add(self.protocol_row)

        self.authgroup_row = Adw.EntryRow(title="Auth group (optional)")
        self.oc_group.add(self.authgroup_row)
        page.add(self.oc_group)

        # -- openvpn3 --------------------------------------------------------
        self.ov_group = Adw.PreferencesGroup(
            title="OpenVPN configuration",
            description="The .ovpn file is imported into openvpn3 on first "
                        "connect, and re-imported whenever it changes on disk.",
        )
        self.config_row = Adw.ActionRow(title="Config file", subtitle="No file chosen")
        browse = Gtk.Button(label="Choose...", valign=Gtk.Align.CENTER)
        browse.connect("clicked", self._on_browse)
        self.config_row.add_suffix(browse)
        self.config_row.set_activatable_widget(browse)
        self.ov_group.add(self.config_row)
        page.add(self.ov_group)

        # -- credentials -----------------------------------------------------
        creds = Adw.PreferencesGroup(
            title="Credentials",
            description="Stored encrypted in the local SQLite database.",
        )
        self.username_row = Adw.EntryRow(title="Username")
        creds.add(self.username_row)
        self.password_row = Adw.PasswordEntryRow(title="Password")
        creds.add(self.password_row)
        page.add(creds)

        # -- advanced --------------------------------------------------------
        advanced = Adw.PreferencesGroup(title="Advanced")
        self.extra_row = Adw.EntryRow(title="Extra command-line arguments")
        advanced.add(self.extra_row)
        self.autoconnect_row = Adw.SwitchRow(
            title="Connect at launch",
            subtitle=f"Bring this VPN up when {APP_NAME} starts",
        )
        advanced.add(self.autoconnect_row)
        page.add(advanced)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page)
        self.set_child(view)

    # ------------------------------------------------------------------- data

    def _load(self) -> None:
        p = self.profile
        self.name_row.set_text(p.name)
        self.kind_row.set_selected(
            next((i for i, (k, _, _) in enumerate(KINDS) if k == p.kind), 0)
        )
        self.host_row.set_text(p.host)
        self.protocol_row.set_selected(
            next((i for i, (k, _) in enumerate(PROTOCOLS) if k == p.protocol), 0)
        )
        self.authgroup_row.set_text(p.authgroup)
        self.username_row.set_text(p.username)
        self.extra_row.set_text(p.extra_args)
        self.autoconnect_row.set_active(p.autoconnect)
        self._set_config_path(p.config_path)

        if p.id is not None:
            try:
                self.password_row.set_text(
                    self.store.get_secret(p.id, db.CRED_PASSWORD) or ""
                )
            except SecretsUnavailable:
                self.password_row.set_text("")
                self.password_row.set_title("Password (previous value unreadable)")
        self._validate()

    def _collect(self) -> Profile:
        p = self.profile
        p.name = self.name_row.get_text().strip()
        p.kind = KINDS[self.kind_row.get_selected()][0]
        p.host = self.host_row.get_text().strip()
        p.protocol = PROTOCOLS[self.protocol_row.get_selected()][0]
        p.authgroup = self.authgroup_row.get_text().strip()
        p.config_path = self._config_path
        p.username = self.username_row.get_text().strip()
        p.extra_args = self.extra_row.get_text().strip()
        p.autoconnect = self.autoconnect_row.get_active()
        return p

    # -------------------------------------------------------------- behaviour

    def _current_kind(self) -> str:
        return KINDS[self.kind_row.get_selected()][0]

    def _sync_kind_visibility(self) -> None:
        kind = self._current_kind()
        self.oc_group.set_visible(kind == db.KIND_OPENCONNECT)
        self.ov_group.set_visible(kind == db.KIND_OPENVPN3)
        self._validate()

    def _set_config_path(self, path: str) -> None:
        self._config_path = path or ""
        self.config_row.set_subtitle(self._config_path or "No file chosen")

    def _on_browse(self, _button) -> None:
        dialog = Gtk.FileDialog(title="Choose an .ovpn configuration")
        ovpn = Gtk.FileFilter(name="OpenVPN configuration (*.ovpn, *.conf)")
        ovpn.add_pattern("*.ovpn")
        ovpn.add_pattern("*.conf")
        every = Gtk.FileFilter(name="All files")
        every.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(ovpn)
        filters.append(every)
        dialog.set_filters(filters)
        dialog.set_default_filter(ovpn)
        if self._config_path:
            dialog.set_initial_file(Gio.File.new_for_path(self._config_path))
        dialog.open(self.get_root(), None, self._on_browse_done)

    def _on_browse_done(self, dialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except Exception:  # noqa: BLE001 - the user pressing Cancel lands here
            return
        if gfile is not None:
            self._set_config_path(gfile.get_path() or "")
            if not self.name_row.get_text().strip():
                self.name_row.set_text(Path(gfile.get_path()).stem)
            self._validate()

    def _validate(self) -> None:
        """Enable Save only when the profile could actually be connected."""
        if not hasattr(self, "save_button"):
            return
        ok = bool(self.name_row.get_text().strip())
        if self._current_kind() == db.KIND_OPENCONNECT:
            ok = ok and bool(self.host_row.get_text().strip())
        else:
            ok = ok and bool(getattr(self, "_config_path", ""))
        self.save_button.set_sensitive(ok)

    def _on_save(self, _button) -> None:
        profile = self._collect()
        try:
            saved = self.store.save_profile(profile)
        except Exception as exc:  # noqa: BLE001 - most likely the UNIQUE on name
            self._report(f"Could not save: {exc}")
            return

        try:
            self.store.set_secret(
                saved.id, db.CRED_PASSWORD, self.password_row.get_text()
            )
        except Exception as exc:  # noqa: BLE001
            self._report(f"Profile saved, but the password could not be: {exc}")
            return

        self.profile = saved
        self.on_saved(saved)
        self.close()

    def _report(self, message: str) -> None:
        alert = Adw.AlertDialog(heading="Problem", body=message)
        alert.add_response("ok", "OK")
        alert.present(self)
