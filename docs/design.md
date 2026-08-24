# MLUEVPN — how it works

Background for the commands in the [README](../README.md). Nothing here is
needed to run or install the app.

## Why the venv flags are mandatory

```bash
uv venv --python /usr/bin/python3 --system-site-packages
```

PyGObject has to come from pacman — building it from PyPI needs
gobject-introspection headers and takes minutes. A plain `uv venv` uses uv's
own downloaded interpreter, which cannot see the system `gi` module, and the
app dies with `ModuleNotFoundError: gi`.

`uv sync` installs the project editable plus the `dev` dependency group, so
edits under `src/mluevpn/` take effect on the next start. A source run and an
installed package coexist — both read the same `~/.local/share/mluevpn`
database.

Create the venv with those flags *before* the first `uv sync`. uv reuses an
existing `.venv` but creates a fresh one without `--system-site-packages`, and
that one cannot see `gi`.

```bash
source .venv/bin/activate && mluevpn     # activated shell
uv run python -m mluevpn                 # explicit module
tail -f ~/.local/state/mluevpn/mluevpn.log
```

## Tasks

uv has no task runner, so [poethepoet](https://poethepoet.natn.io/) provides
one from `pyproject.toml`. It is in the `dev` dependency group — the wheel and
the Arch package never see it.

| Task | Runs |
| --- | --- |
| `uv run poe app` | `python -m mluevpn` |
| `uv run poe build` | `./packaging/build.sh` |
| `uv run poe install` | `./packaging/build.sh -si` |
| `uv run poe wheel` | `uv build` → `dist/` |
| `uv run poe clean` | removes `dist/`, makepkg scratch dirs, staged tarball |

Arguments pass through, so `uv run poe build -s` reaches makepkg. The scripts
still work directly (`./packaging/build.sh -si`) — poe is a shortcut, not a
wrapper that hides anything.

## Why the build must not see a virtualenv

`python -m installer` derives its install paths from the *running* interpreter.
Build with a venv active and the files land under that venv's prefix
(`home/you/project/.venv/lib/…`) instead of `/usr/lib`: no `/usr/bin/mluevpn`,
so the desktop entry's `Exec=mluevpn` finds nothing, and on someone else's
machine it tries to create a `/home/you/` tree that isn't theirs.

The PKGBUILD calls `/usr/bin/python` explicitly, and `build.sh` strips
`VIRTUAL_ENV` before calling makepkg. To check a package before handing it
over — every path must start with `usr/`:

```bash
tar -tf packaging/mluevpn-*.pkg.tar.zst | grep -c '^home/'   # must print 0
```

makepkg also refuses to run as root, which is why `build.sh` is never sudo'd.

The package is `arch=('any')`, so one file works on any Arch box (Omarchy,
EndeavourOS, CachyOS, vanilla). pacman resolves `python-gobject`, `gtk4`,
`libadwaita`, `openconnect` and the rest from the official repos; `openvpn3` is
AUR-only, so it stays an `optdepends` and the recipient runs `yay -S
openvpn3-git` themselves.

## The two version numbers

`version` in `pyproject.toml` and `pkgver` in `packaging/PKGBUILD` are not
linked. Changing only one produces a package whose name disagrees with what it
contains. Bump both, rebuild, then `pacman -U` the new file over the old one.

## Backends

**OpenConnect.** Spawns `sudo openconnect --protocol=… <host>` under a pty and
answers the prompts from the credential store:

| Prompt | Answered with |
| --- | --- |
| `[sudo] password for you:` | the stored sudo password |
| `Enter 'yes' to accept…` | `yes`, and the `pin-sha256:` fingerprint is captured |
| `Username:` | the profile's username |
| `Password:` | the stored password |

After the first successful connect the pinned fingerprint is passed as
`--servercert`. That removes the certificate prompt *and* upgrades a blind
"yes" into a real identity check — a swapped server certificate now fails the
connection instead of silently succeeding. Disconnecting sends `SIGINT` to the
`openconnect` process (found via `pgrep -P` on the `sudo` child) so the tunnel
is torn down cleanly rather than killed.

**OpenVPN 3.** Imports the `.ovpn` under a namespaced config name
(`mluevpn-<profile>`), re-importing whenever the file changes, then drives
`openvpn3 session-start`. No sudo — openvpn3 runs as your user via its session
manager, which is then polled so the UI notices a tunnel that drops on its own.

## Credential storage

Secrets are Fernet tokens in SQLite. The key is not in the database — it lives
in your system keyring (gnome-keyring via libsecret):

```console
$ sqlite3 ~/.local/share/mluevpn/mluevpn.db 'SELECT key, value_enc FROM credentials'
password|gAAAAABqi7uKp8KZynUMYDDoiJy2cIPVDqNZoL_V_M976DyNJjji…
```

With no keyring reachable (TTY-only login, locked session) the key falls back
to `~/.local/share/mluevpn/master.key` at mode 0600 and the window says so.
Passwords are redacted from the log view before display.

This does **not** protect against anything running as your user or as root —
either can ask the keyring for the key. It raises the bar over a plaintext
file; it is not a defence against a compromised account.

Everything is per-user, including two accounts on the same machine. Copying the
database to someone else is useless to them anyway: the key is in *your*
keyring.

| Path | What |
| --- | --- |
| `~/.local/share/mluevpn/mluevpn.db` | profiles + encrypted credentials (0600) |
| `~/.local/share/mluevpn/master.key` | only if no keyring was available (0600) |
| `~/.local/state/mluevpn/mluevpn.log` | application log |
| keyring `mluevpn` / `master-key` | the Fernet key |

To wipe everything: delete the data directory and
`secret-tool clear service mluevpn username master-key`.

## System check

**System check…** in the main menu lists every program MLUEVPN drives with its
pacman package, version, and whether an update is waiting, plus a copy button
for the exact install command. A missing program or pending update raises a
banner at startup, so you find out before a connection fails.

Two limits, both stated on the page:

- Updates compare against pacman's **local** sync database, so the answer is
  only as fresh as your last `pacman -Sy`. The app will not refresh it — that
  needs root and changes system state.
- `openvpn3` is AUR-only, so there is no repo version to compare against. It
  shows **Installed** rather than *Up to date*; check with `yay -Sua`.

## The theme toggle

Omarchy themes GTK by writing `~/.config/gtk-4.0/gtk.css` full of
`@define-color` overrides, which load at GTK's `USER` priority and outrank
libadwaita — so asking libadwaita for the light scheme sets the scheme but
leaves every colour pinned dark. A `@define-color` cannot be un-defined, so
forcing a scheme means supplying the whole palette at a higher priority, which
is what `ui/theme.py` does. **Follow system** installs nothing and lets your
Omarchy theme through.

## Git

```bash
git init && git add . && git commit -m "MLUEVPN 0.1.0"
git remote add origin <your-remote> && git push -u origin main
```

Committed: `src/`, `pyproject.toml`, `uv.lock`, `packaging/`, `README.md`,
`docs/`, `LICENSE` — enough for anyone to clone and build. Ignored:

| Ignored | Why |
| --- | --- |
| `*.db`, `master.key`, `*.key`, `*.pem`, `*.ovpn`, `.env` | Credentials and private keys |
| `.venv/`, `venv/`, `.direnv/` | Machine-specific; must never reach a build |
| `__pycache__/`, `dist/`, `build/`, `*.egg-info/` | Regenerated every build |
| `packaging/src/`, `packaging/pkg/`, `*.pkg.tar.zst`, `*.tar.gz` | makepkg output and scratch dirs |
| `.ruff_cache/`, `.mypy_cache/`, `.pytest_cache/`, `.idea/`, `.vscode/` | Tooling and editor state |

The built `.pkg.tar.zst` is deliberately not committed — attach it to a release
or send it directly.

Before publishing publicly: `url=` in `packaging/PKGBUILD` points at a
repository that does not exist, and `Maintainer:` carries a personal email
address. For the AUR, point `source=()` at a release tarball or a
`git+https://…` URL and regenerate `.SRCINFO` with
`makepkg --printsrcinfo > .SRCINFO`.

## Layout

```
src/mluevpn/
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
