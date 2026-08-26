#!/usr/bin/env bash
# Build the Arch package: stage a clean source tarball, then run makepkg.
#
#   ./packaging/build.sh          -> produces mluevpn-<ver>-1-any.pkg.tar.zst
#   ./packaging/build.sh -i       -> ...and installs it (asks for sudo)
#
# Never run this with sudo; makepkg refuses to build as root.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(dirname "$here")
name=$(sed -n 's/^pkgname=//p' "$here/PKGBUILD")

# pyproject.toml is the single source of truth for the version. The PKGBUILD
# still carries a literal pkgver -- it has to stand on its own for the AUR --
# so sync it here rather than making it parse anything at build time.
ver=$(sed -n '/^\[project\]/,/^\[/{s/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p}' \
        "$root/pyproject.toml")
if [[ -z "$ver" ]]; then
  echo "error: no version found in $root/pyproject.toml" >&2
  exit 1
fi
# pkgver forbids hyphens, so a PEP 440 pre-release like 0.2.0-rc1 becomes 0.2.0_rc1.
ver=${ver//-/_}

cur=$(sed -n 's/^pkgver=//p' "$here/PKGBUILD")
if [[ "$cur" != "$ver" ]]; then
  echo "version: $cur -> $ver (from pyproject.toml)"
  sed -i "s/^pkgver=.*/pkgver=$ver/" "$here/PKGBUILD"
  # A new upstream version restarts the package revision, per Arch convention.
  sed -i "s/^pkgrel=.*/pkgrel=1/" "$here/PKGBUILD"
fi
rel=$(sed -n 's/^pkgrel=//p' "$here/PKGBUILD")

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/$name-$ver"

# Copy the working tree minus everything that should not ship: the dev venv,
# byte-code, and any previous build output.
tar -C "$root" \
    --exclude=.venv \
    --exclude=.git \
    --exclude=__pycache__ \
    --exclude='*.pyc' \
    --exclude=dist \
    --exclude=build \
    --exclude=pkg \
    --exclude=src \
    --exclude='*.tar.gz' \
    --exclude='*.pkg.tar.zst' \
    --exclude='*.egg-info' \
    -cf - --transform='' . \
  | tar -C "$stage/$name-$ver" -xf -

# The exclude above drops packaging/src (makepkg's scratch dir) but we still
# need the actual Python sources back.
tar -C "$root" --exclude=__pycache__ --exclude='*.pyc' -cf - src \
  | tar -C "$stage/$name-$ver" -xf -

tar -C "$stage" -czf "$here/$name-$ver.tar.gz" "$name-$ver"
echo "staged source: $here/$name-$ver.tar.gz"

cd "$here"
# Drop any active virtualenv before handing off to makepkg: an inherited
# VIRTUAL_ENV/PATH would otherwise decide where the package's files land.
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  echo "note: ignoring active virtualenv $VIRTUAL_ENV for the build"
  PATH=$(printf '%s' "$PATH" | tr ':' '\n' | grep -vF "$VIRTUAL_ENV" | paste -sd:)
  export PATH
fi
env -u VIRTUAL_ENV makepkg --force --cleanbuild "$@"

echo
echo "built: $(ls -1t "$here/$name-$ver-$rel-"*.pkg.tar.zst | head -1)"
