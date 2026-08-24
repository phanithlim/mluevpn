# MLUEVPN

GTK4 VPN manager for **OpenConnect** (GlobalProtect) and **OpenVPN 3**, with
credentials saved encrypted so connecting is one click.

## Requirements

```bash
sudo pacman -S --needed python-gobject gtk4 libadwaita openconnect sudo
yay -S openvpn3-git                      # AUR-only
```

## Run from source

```bash
uv venv --python /usr/bin/python3 --system-site-packages
uv sync
uv run poe app
```

## Build and install

```bash
sudo pacman -S --needed base-devel
uv run poe install                       # build the pacman package + install it
```

Other tasks: `poe build` (build only), `poe wheel` (Python wheel into `dist/`),
`poe clean`. All defined in `pyproject.toml`.

## Give it to someone else

```bash
uv run poe build                                              # you
scp packaging/mluevpn-*.pkg.tar.zst them@their-box:~/

sudo pacman -U ~/mluevpn-*.pkg.tar.zst                        # them
yay -S openvpn3-git
```

## Gotchas

- The two `uv venv` flags are required. Without them `uv` uses its own Python,
  which can't see the system `gi` module → `ModuleNotFoundError: gi`.
- Never run the build with sudo, and deactivate your venv first — makepkg
  refuses to run as root.
- Shipping an update: bump `version` in `pyproject.toml` **and** `pkgver` in
  `packaging/PKGBUILD`.
- Never share `~/.local/share/mluevpn/mluevpn.db`, `master.key`, or `.ovpn`
  files — credentials and private keys.

## In the app

**+** adds a VPN · **Ctrl+L** shows the log · **System check…** in the menu
tells you if openconnect/openvpn3 are installed or need an update.

Data lives in `~/.local/share/mluevpn/` (database, 0600) and
`~/.local/state/mluevpn/mluevpn.log`. The encryption key is in your keyring.

---

How it works, why the flags are what they are, and what goes in git:
**[docs/design.md](docs/design.md)**. Licence: [MIT](LICENSE).
