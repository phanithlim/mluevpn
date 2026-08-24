"""Filesystem locations used by myvpn."""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "mluevpn"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "mluevpn"

DB_PATH = DATA_DIR / "mluevpn.db"
KEY_FALLBACK_PATH = DATA_DIR / "master.key"
LOG_PATH = STATE_DIR / "mluevpn.log"


def ensure_dirs() -> None:
    """Create the data/state directories with private permissions."""
    for d in (DATA_DIR, STATE_DIR):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
