"""The System check page: what MLUEVPN needs, and whether you have it."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import deps  # noqa: E402
from ..crypto import KeySource  # noqa: E402

STATE_LABEL = {
    deps.OK: "Up to date",
    deps.INSTALLED: "Installed",
    deps.MISSING: "Missing",
    deps.OUTDATED: "Update",
    deps.UNKNOWN: "Unknown",
}

# INSTALLED is a good outcome, so it never gets a warning colour; the accent
# only marks it as "present, but pacman has no repo version to compare with".
STATE_CSS = {
    deps.OK: "success",
    deps.INSTALLED: "accent",
    deps.MISSING: "error",
    deps.OUTDATED: "warning",
    deps.UNKNOWN: "dimmed",
}


def _pill(state: str) -> Gtk.Label:
    return _label_pill(STATE_LABEL[state], STATE_CSS[state])


def _label_pill(text: str, css: str) -> Gtk.Label:
    pill = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    pill.add_css_class("status-pill")
    pill.add_css_class(css)
    return pill


class DepsDialog(Adw.Dialog):
    """Lists the external programs the app drives and their install state."""

    def __init__(self, key_source: KeySource | None = None) -> None:
        super().__init__()
        self.key_source = key_source

        self.set_title("System check")
        self.set_content_width(560)
        self.set_content_height(620)

        self._build()
        self.refresh()

    # ----------------------------------------------------------------- layout

    def _build(self) -> None:
        header = Adw.HeaderBar()
        self.refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text="Check again"
        )
        self.refresh_button.connect("clicked", lambda *_: self.refresh())
        header.pack_end(self.refresh_button)

        self.banner = Adw.Banner(revealed=False)

        self.required_group = Adw.PreferencesGroup(
            title="Required",
            description="MLUEVPN drives these programs — connecting needs them.",
        )
        self.optional_group = Adw.PreferencesGroup(
            title="Optional",
            description="Nice to have, but nothing depends on them.",
        )
        self.storage_group = Adw.PreferencesGroup(
            title="Credential storage",
            description="Where the key that encrypts your saved passwords lives.",
        )

        self.page = Adw.PreferencesPage()
        self.page.add(self.required_group)
        self.page.add(self.optional_group)
        self.page.add(self.storage_group)

        self.spinner = Adw.StatusPage(
            title="Checking…",
            child=Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, height_request=32),
        )

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.spinner, "busy")
        self.stack.add_named(self.page, "results")
        self.stack.set_visible_child_name("busy")

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        view.set_content(self.stack)

        self.toasts = Adw.ToastOverlay(child=view)
        self.set_child(self.toasts)

    # ---------------------------------------------------------------- probing

    def refresh(self) -> None:
        """Re-probe on a worker thread; pacman calls are slow enough to block."""
        self.refresh_button.set_sensitive(False)
        self.stack.set_visible_child_name("busy")
        threading.Thread(target=self._probe, daemon=True).start()

    def _probe(self) -> None:
        try:
            statuses = deps.check_all()
        except Exception as exc:  # noqa: BLE001 - worker thread must not die
            GLib.idle_add(self._probe_failed, str(exc))
            return
        GLib.idle_add(self._show, statuses)

    def _probe_failed(self, message: str) -> bool:
        self.refresh_button.set_sensitive(True)
        self.stack.set_visible_child_name("results")
        self.banner.set_title(f"Could not run the check: {message}")
        self.banner.set_revealed(True)
        return GLib.SOURCE_REMOVE

    def _show(self, statuses: list[deps.DepStatus]) -> bool:
        for group in (self.required_group, self.optional_group, self.storage_group):
            self._clear(group)

        counts = {self.required_group: 0, self.optional_group: 0}
        for status in statuses:
            group = (
                self.required_group
                if status.dependency.required
                else self.optional_group
            )
            group.add(self._row(status))
            counts[group] += 1

        # Every dependency is required today, so the Optional group would
        # otherwise show as a heading with nothing under it.
        for group, count in counts.items():
            group.set_visible(count > 0)

        self.storage_group.add(self._storage_row())
        self._update_banner(statuses)

        self.stack.set_visible_child_name("results")
        self.refresh_button.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _clear(group: Adw.PreferencesGroup) -> None:
        """Drop every row we previously added to `group`.

        PreferencesGroup has no clear(); it keeps its rows in an internal
        ListBox, so collect them first and then remove them (removing while
        walking the sibling chain would cut the walk short).
        """
        listbox = _find_listbox(group)
        if listbox is None:
            return
        rows = []
        row = listbox.get_first_child()
        while row is not None:
            rows.append(row)
            row = row.get_next_sibling()
        for row in rows:
            group.remove(row)

    def _row(self, status: deps.DepStatus) -> Adw.ActionRow:
        dep = status.dependency
        row = Adw.ActionRow(title=dep.name, subtitle=status.summary())
        row.set_subtitle_lines(2)

        detail = dep.purpose
        if status.path:
            detail += f"\n{status.path}"
        for note in status.notes:
            detail += f"\n{note}"
        row.set_tooltip_text(detail)

        row.add_suffix(_pill(status.state))

        if status.state in (deps.MISSING, deps.OUTDATED):
            command = (
                dep.install_command
                if status.state == deps.MISSING
                else self._update_command(status)
            )
            button = Gtk.Button(
                icon_name="edit-copy-symbolic",
                tooltip_text=f"Copy “{command}”",
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
            )
            button.connect("clicked", lambda _b, c=command: self._copy(c))
            row.add_suffix(button)

        return row

    @staticmethod
    def _update_command(status: deps.DepStatus) -> str:
        if status.dependency.from_aur:
            return "yay -Sua"
        return f"sudo pacman -Syu {status.package or status.dependency.binary}"

    def _storage_row(self) -> Adw.ActionRow:
        source = self.key_source
        if source is None:
            row = Adw.ActionRow(
                title="Encryption key", subtitle="Not determined yet"
            )
            row.add_suffix(_pill(deps.UNKNOWN))
            return row

        if source.is_secure:
            row = Adw.ActionRow(
                title="System keyring",
                subtitle=f"Encryption key held by {source.detail}",
            )
            row.add_suffix(_pill(deps.OK))
        else:
            row = Adw.ActionRow(
                title="Key file (no keyring available)",
                subtitle=f"{source.detail} — mode 0600. Install and unlock "
                         f"gnome-keyring for stronger protection.",
            )
            row.set_subtitle_lines(2)
            # Not an "update" in the pacman sense, so it gets its own wording.
            row.add_suffix(_label_pill("Weaker", "warning"))
        return row

    def _update_banner(self, statuses: list[deps.DepStatus]) -> None:
        missing = deps.missing_required(statuses)
        if missing:
            names = ", ".join(s.dependency.name for s in missing)
            self.banner.set_title(f"{names} is not installed — connecting will fail")
            self.banner.set_revealed(True)
            return

        outdated = [s for s in statuses if s.state == deps.OUTDATED]
        if outdated:
            names = ", ".join(s.dependency.name for s in outdated)
            self.banner.set_title(f"Update available for {names}")
            self.banner.set_revealed(True)
            return

        self.banner.set_revealed(False)

    # --------------------------------------------------------------- helpers

    def _copy(self, command: str) -> None:
        clipboard = self.get_clipboard()
        if clipboard is not None:
            clipboard.set(command)
        self.toasts.add_toast(Adw.Toast(title=f"Copied: {command}", timeout=4))


def _find_listbox(widget: Gtk.Widget) -> Gtk.ListBox | None:
    """Depth-first search for the ListBox inside an Adw.PreferencesGroup."""
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.ListBox):
            return child
        found = _find_listbox(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None
