# MLUEVPN — how it works

Background for the commands in the [README](../README.md). None of this is
needed to run or install the app.

## The venv flags

```bash
uv venv --python /usr/bin/python3 --system-site-packages
```

PyGObject must come from pacman — building it from PyPI needs
gobject-introspection headers and takes minutes. A plain `uv venv` uses uv's own
interpreter, which can't see the system `gi` module.

Create the venv with both flags *before* the first `uv sync`. uv reuses an
existing `.venv` but creates a fresh one without `--system-site-packages`.

A source run and an installed copy share one `~/.local/share/mluevpn` database,
but not the process: the shared `application_id` makes GTK raise the running
window instead of starting a second one.

## Why the build must not see a virtualenv

`python -m installer` derives its install paths from the *running* interpreter.
Build with a venv active and files land under that venv's prefix instead of
`/usr/lib` — no `/usr/bin/mluevpn`, so the desktop entry's `Exec=` finds
nothing, and on someone else's machine it tries to create a `/home/you/` tree.

The PKGBUILD calls `/usr/bin/python` explicitly and `build.sh` strips
`VIRTUAL_ENV` before makepkg. To verify a package — every path must start with
`usr/`:

```bash
tar -tf packaging/mluevpn-*.pkg.tar.zst | grep -c '^home/'   # must print 0
```

The package is `arch=('any')`, so one file works on any Arch box. pacman
resolves the GTK and OpenConnect dependencies; `openvpn3` is AUR-only, so it
stays an `optdepends`.

## One version number

`version` in `pyproject.toml` is the single source of truth.

- `packaging/build.sh` reads it and rewrites `pkgver` in the PKGBUILD, resetting
  `pkgrel` to 1 when the version changes. The PKGBUILD keeps a literal `pkgver`
  so it still stands alone for the AUR.
- `src/mluevpn/__init__.py` reads the *installed* metadata via
  `importlib.metadata`, so the About dialog can't disagree with the package. The
  lookup key is `__name__`, so the name isn't hardcoded either.

## Backends

**OpenConnect.** Spawns `sudo openconnect --protocol=… <host>` under a pty and
answers prompts from the credential store: the sudo password, the certificate
prompt, then username and password.

The `pin-sha256:` fingerprint captured on first connect is passed as
`--servercert` thereafter. That removes the prompt *and* upgrades a blind "yes"
into a real identity check — a swapped certificate now fails instead of
silently succeeding. Disconnecting sends `SIGINT` to the `openconnect` process
(found via `pgrep -P` on the sudo child) so the tunnel closes cleanly.

**OpenVPN 3.** Imports the `.ovpn` under a namespaced config name
(`mluevpn-<profile>`), re-importing when the file changes, then drives
`openvpn3 session-start`. No sudo — openvpn3 runs as your user via its session
manager, which is polled so the UI notices a tunnel that drops on its own.

## Credential storage

Secrets are Fernet tokens in SQLite. The key is not in the database — it lives
in your system keyring (gnome-keyring via libsecret). With no keyring reachable
it falls back to `~/.local/share/mluevpn/master.key` at 0600, and the window
says so. Passwords are redacted from the log view.

| Path | What |
| --- | --- |
| `~/.local/share/mluevpn/mluevpn.db` | profiles + encrypted credentials (0600) |
| `~/.local/share/mluevpn/master.key` | only if no keyring was available (0600) |
| `~/.local/state/mluevpn/mluevpn.log` | application log |
| keyring `mluevpn` / `master-key` | the Fernet key |

This does **not** protect against anything running as your user or as root —
either can ask the keyring for the key. It raises the bar over a plaintext
file; it is not a defence against a compromised account.

To wipe everything: delete the data directory and
`secret-tool clear service mluevpn username master-key`.

## System check

**System check…** lists every program MLUEVPN drives with its package, version,
and whether an update is waiting. A missing program or pending update raises a
startup banner, so you find out before a connection fails.

Two limits, both stated on the page: updates compare against pacman's *local*
sync database, so freshness depends on your last `pacman -Sy`; and `openvpn3`
is AUR-only, so it shows **Installed** rather than *Up to date*.

## The theme toggle

Omarchy themes GTK by writing `~/.config/gtk-4.0/gtk.css` full of
`@define-color` overrides, which load at GTK's `USER` priority and outrank
libadwaita — so asking libadwaita for the light scheme leaves every colour
pinned dark. A `@define-color` can't be un-defined, so forcing a scheme means
supplying the whole palette at higher priority, which is what `ui/theme.py`
does. **Follow system** installs nothing.

## What's in git

Committed: `src/`, `pyproject.toml`, `uv.lock`, `packaging/`, `README.md`,
`docs/`, `LICENSE` — enough for anyone to clone and build.

Ignored: credentials and keys (`*.db`, `master.key`, `*.pem`, `*.ovpn`,
`.env`), machine-specific dirs (`.venv/`, `.direnv/`), build output
(`__pycache__/`, `dist/`, `packaging/src/`, `packaging/pkg/`, `*.pkg.tar.zst`),
and tooling caches.

The built `.pkg.tar.zst` is deliberately not committed — attach it to a release
or send it directly.

Before publishing: `url=` in the PKGBUILD points at a repository that doesn't
exist yet, and `Maintainer:` carries a personal email address. For the AUR,
point `source=()` at a release tarball or `git+https://…` URL and regenerate
`.SRCINFO` with `makepkg --printsrcinfo > .SRCINFO`.

## Layout

```
src/mluevpn/
├── __init__.py          name/version/description, read from package metadata
├── config.py            XDG paths
├── crypto.py            keyring-backed Fernet, with a 0600 file fallback
├── db.py                profiles, encrypted credentials, settings, history
├── deps.py              probes openconnect/openvpn3, asks pacman for updates
├── manager.py           owns running backends, marshals events onto the GTK loop
├── backends/
│   ├── base.py          worker-thread lifecycle + state machine
│   ├── openconnect.py   the pexpect prompt driver
│   └── openvpn3.py      config import + session management
└── ui/
    ├── app.py           Adw.Application, stylesheet loading, shutdown
    ├── window.py        VPN list, status pills, log pane
    ├── deps_dialog.py   the System check page
    ├── profile_dialog.py
    ├── theme.py         light/dark/system, over the desktop theme
    └── style.css
```

Backends run on worker threads and never touch GTK; `manager.py` is the seam
that hands every callback back to the main loop via `GLib.idle_add`.
