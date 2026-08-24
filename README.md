# MLUEVPN

A GTK4 front-end for two VPNs: an **OpenConnect** (GlobalProtect) server that
needs root and answers three interactive prompts, and an **OpenVPN 3** `.ovpn`
profile. Credentials are saved once, encrypted, in a local SQLite database — so
connecting is one click instead of a sudo prompt followed by `yes`, a username
and a password.

Built for Omarchy (Arch + Hyprland). GTK4/libadwaita is Wayland-native, so no
XWayland shim and fractional scaling behaves.

---

## Requirements

```bash
sudo pacman -S --needed python-gobject gtk4 libadwaita openconnect sudo
yay -S openvpn3-git      # openvpn3 is AUR-only
```

The app checks for these itself — **System check…** in the main menu says what
is installed, what is missing, and what has an update waiting.

## Run it locally

For development. Nothing is installed system-wide.

```bash
uv venv --python /usr/bin/python3 --system-site-packages
uv pip install -e .
uv run mluevpn
```

> `--python /usr/bin/python3 --system-site-packages` is not optional. A plain
> `uv venv` uses uv's own interpreter, which cannot see the system `gi` module,
> and the app dies with `ModuleNotFoundError: gi`. PyGObject has to come from
> pacman — building it from PyPI needs gobject-introspection headers.

`-e` is an editable install: edit anything under `src/mluevpn/` and it takes
effect the next time you start the app. Other ways in:

```bash
source .venv/bin/activate && mluevpn     # activated shell
uv run python -m mluevpn                 # same entry point, explicit module
tail -f ~/.local/state/mluevpn/mluevpn.log
```

A source run and an installed package coexist fine — both read the same
`~/.local/share/mluevpn` database.

## Build the package

This is the normal way to install it, and the only sane way to hand it to
somebody else: pacman pulls in every dependency, and `pacman -R mluevpn`
removes it cleanly.

```bash
sudo pacman -S --needed base-devel
./packaging/build.sh -si       # -s pulls build deps, -i installs the result
```

That leaves `packaging/mluevpn-0.1.0-1-any.pkg.tar.zst`, puts `mluevpn` on
`$PATH`, and adds MLUEVPN to your app launcher. Drop `-i` to build without
installing; install later with `sudo pacman -U packaging/mluevpn-*.pkg.tar.zst`.

Two rules:

- **Don't run it with sudo** — makepkg refuses to build as root.
- **Deactivate your venv first.** `build.sh` strips `VIRTUAL_ENV` and works
  around it, but the habit is worth having.

Sanity-check a package before handing it over — every path must live under
`usr/`:

```bash
tar -tf packaging/mluevpn-0.1.0-1-any.pkg.tar.zst | grep -c '^home/'   # must print 0
```

> **Why that check exists.** `python -m installer` derives its install paths
> from the *running* interpreter. Build with a virtualenv active and the files
> land under that venv's prefix (`home/you/project/.venv/lib/…`) instead of
> `/usr/lib` — no `/usr/bin/mluevpn`, and a package that tries to create a
> `/home/you/` tree on someone else's machine. The PKGBUILD calls
> `/usr/bin/python` explicitly to prevent this.

## Share it with someone else

### Send them the package

Simplest — they need no build tools and no Python knowledge.

```bash
# you
./packaging/build.sh
scp packaging/mluevpn-0.1.0-1-any.pkg.tar.zst coworker@their-box:~/

# them
sudo pacman -U ~/mluevpn-0.1.0-1-any.pkg.tar.zst
yay -S openvpn3-git      # only if they use the OpenVPN side
```

pacman resolves `python-gobject`, `gtk4`, `libadwaita`, `openconnect` and the
rest from the official repos. The package is `arch=('any')`, so the same file
works on any Arch box (Omarchy, EndeavourOS, CachyOS, vanilla).

### Or share the source

Better once more than one person changes it — updates become a `git pull`:

```bash
sudo pacman -S --needed base-devel
git clone <your-repo> && cd mluevpn
./packaging/build.sh -si
```

**To ship an update**, bump `version` in `pyproject.toml` **and** `pkgver` in
`packaging/PKGBUILD`, rebuild, and `pacman -U` the new file over the old one.
The two numbers are not linked — changing only one produces a package whose
name disagrees with what it contains.

### Never share these

- **`~/.local/share/mluevpn/mluevpn.db`** — your encrypted credentials. It
  would be useless to them anyway; the key is in *your* keyring, not the file.
- **`~/.local/share/mluevpn/master.key`** — the key itself, if you have no
  keyring.
- **`.ovpn` files** — they usually embed a private key and certificate.

Everything MLUEVPN stores is per-user. Each person adds their own profiles and
their own sudo password on first run.

### Publishing it publicly

Two placeholders are fine for a package on a USB stick, but not for a public
repo: `url=` in `packaging/PKGBUILD` points at a repository that does not
exist, and `Maintainer:` carries a personal email address. Fix both before
pushing. For the AUR, point `source=()` at a release tarball or a
`git+https://…` URL and regenerate `.SRCINFO` with
`makepkg --printsrcinfo > .SRCINFO`.

## Putting it on GitHub

```bash
git init && git add . && git commit -m "MLUEVPN 0.1.0"
git remote add origin <your-remote> && git push -u origin main
```

**Committed:** `src/`, `pyproject.toml`, `uv.lock` (so everyone resolves the
same versions), `packaging/` (PKGBUILD, build.sh, the .desktop file),
`README.md`, `LICENSE`. That set is enough for anyone to clone and build.

**Ignored** by `.gitignore`:

| Ignored | Why |
| --- | --- |
| `*.db`, `master.key`, `*.key`, `*.pem`, `*.ovpn`, `.env` | Credentials and private keys — never in a repo, public or not |
| `.venv/`, `venv/`, `.direnv/` | Machine-specific, and must never reach a package build |
| `__pycache__/`, `dist/`, `build/`, `*.egg-info/` | Regenerated by every build |
| `packaging/src/`, `packaging/pkg/`, `*.pkg.tar.zst`, `*.tar.gz` | makepkg's output and scratch dirs |
| `.ruff_cache/`, `.mypy_cache/`, `.pytest_cache/`, `.idea/`, `.vscode/` | Tooling and editor state |
| `*.log`, `.DS_Store`, `*.swp`, `*~` | Noise |

The built `.pkg.tar.zst` is deliberately not committed — it's a binary that
changes every build. Attach it to a release instead, or send it directly.

## Using it

1. **+** (or Ctrl+N) to add a VPN. Pick OpenConnect (host + protocol) or
   OpenVPN 3 (choose the `.ovpn`), then fill in username and password.
2. The first time you connect an OpenConnect profile it asks for your sudo
   password once and saves it. A banner offers this up front.
3. **Connect**. Watch the log (Ctrl+L) if something goes wrong — it opens
   automatically on failure.

Clicking a row opens its editor; the ⋮ menu has *Forget saved credentials* and
*Delete*; **Appearance** switches light/dark/system; closing the window while a
tunnel is up asks whether to disconnect first rather than orphaning it.

### System check

**System check…** in the main menu lists every program MLUEVPN drives with its
pacman package, its version, and whether an update is waiting — plus a copy
button for the exact install or update command. A missing program or a pending
update also raises a banner on the main window at startup, so you find out
before a connection fails rather than during one.

Two things it cannot tell you, both stated on the page:

- Updates are compared against pacman's **local** sync database, so the answer
  is only as fresh as your last `pacman -Sy`. The app will not refresh it —
  that needs root and changes system state.
- `openvpn3` comes from the AUR, which has no repo version to compare against.
  It shows **Installed** rather than *Up to date*; check with `yay -Sua`.

## How it works

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

**Credentials** are stored as Fernet tokens in SQLite. The key is not in the
database — it lives in your system keyring (gnome-keyring via libsecret):

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

### The theme toggle

Omarchy themes GTK by writing `~/.config/gtk-4.0/gtk.css` full of
`@define-color` overrides, which load at GTK's `USER` priority and outrank
libadwaita — so asking libadwaita for the light scheme sets the scheme but
leaves every colour pinned dark. A `@define-color` cannot be un-defined, so
forcing a scheme means supplying the whole palette at a higher priority, which
is what `ui/theme.py` does. **Follow system** installs nothing and lets your
Omarchy theme through.

## Project layout

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

## Data files

| Path | What |
| --- | --- |
| `~/.local/share/mluevpn/mluevpn.db` | profiles + encrypted credentials (0600) |
| `~/.local/state/mluevpn/mluevpn.log` | application log |
| `~/.local/share/mluevpn/master.key` | only if no keyring was available (0600) |
| keyring `mluevpn` / `master-key` | the Fernet key |

To wipe everything: delete the data directory and
`secret-tool clear service mluevpn username master-key`.

## License

MIT — see [LICENSE](LICENSE).
# mluevpn
