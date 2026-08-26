"""Filesystem locations used by MLUEVPN.

Directory and file names follow the distribution name from pyproject.toml, so a
rename there carries through instead of leaving stale paths behind.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import PACKAGE_NAME


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / PACKAGE_NAME
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / PACKAGE_NAME

DB_PATH = DATA_DIR / f"{PACKAGE_NAME}.db"
KEY_FALLBACK_PATH = DATA_DIR / "master.key"
LOG_PATH = STATE_DIR / f"{PACKAGE_NAME}.log"


def ensure_dirs() -> None:
    """Create the data/state directories with private permissions."""
    for d in (DATA_DIR, STATE_DIR):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
