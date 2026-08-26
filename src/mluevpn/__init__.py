"""MLUEVPN - a small GTK4 front-end for OpenConnect and OpenVPN3.

Everything nameable about the app comes from the packaging metadata, which
hatchling generates from ``[project]`` in ``pyproject.toml``. Bumping the
version or renaming the project there is enough -- nothing here needs editing,
and the About dialog can never drift out of sync with the built package.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, metadata

# The import name doubles as the distribution name (see [tool.hatch.build] in
# pyproject.toml), so we can look ourselves up without hardcoding a string.
_DIST = __name__

try:
    _meta = metadata(_DIST)
    PACKAGE_NAME: str = _meta["Name"]
    __version__: str = _meta["Version"]
    DESCRIPTION: str = _meta["Summary"] or ""
except PackageNotFoundError:  # pragma: no cover - source tree, never installed
    # `uv sync` and the pacman package both register metadata, so this is only
    # reached by running out of a bare checkout with no install at all.
    PACKAGE_NAME = _DIST
    __version__ = "0.0.0+source"
    DESCRIPTION = "GTK4 VPN manager for OpenConnect and OpenVPN 3"

# Display name. The distribution name is lowercase by packaging convention;
# the app has always shown it shouted.
APP_NAME: str = PACKAGE_NAME.upper()

# The description in pyproject.toml leads with the app's own name, which would
# read as a stutter next to it in the About dialog. Drop that prefix.
if DESCRIPTION.upper().startswith(f"{APP_NAME} - "):
    DESCRIPTION = DESCRIPTION[len(APP_NAME) + 3:]

APP_ID = "dev.mlue.MlueVpn"
