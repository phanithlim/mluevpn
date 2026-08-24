"""SQLite-backed profile and credential store.

Layout:
  profiles     one row per VPN, plus the non-secret connection settings
  credentials  per-profile secrets, Fernet-encrypted (password, cert pin)
  app_secrets  process-wide secrets, currently just the sudo password
  history      a short connection log, handy when a VPN starts misbehaving
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

from .config import DB_PATH, ensure_dirs
from .crypto import SecretBox

SCHEMA_VERSION = 1

KIND_OPENCONNECT = "openconnect"
KIND_OPENVPN3 = "openvpn3"

# Secret names used in the credentials table.
CRED_PASSWORD = "password"
CRED_SERVERCERT = "servercert"

SUDO_PASSWORD = "sudo_password"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL CHECK (kind IN ('openconnect', 'openvpn3')),
    host        TEXT NOT NULL DEFAULT '',
    protocol    TEXT NOT NULL DEFAULT 'gp',
    authgroup   TEXT NOT NULL DEFAULT '',
    config_path TEXT NOT NULL DEFAULT '',
    username    TEXT NOT NULL DEFAULT '',
    extra_args  TEXT NOT NULL DEFAULT '',
    autoconnect INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value_enc  BLOB NOT NULL,
    PRIMARY KEY (profile_id, key)
);

CREATE TABLE IF NOT EXISTS app_secrets (
    key       TEXT PRIMARY KEY,
    value_enc BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
    ts         REAL NOT NULL,
    event      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS history_by_time ON history (ts DESC);
"""


@dataclass
class Profile:
    """A configured VPN. Secrets are never held on this object."""

    id: int | None = None
    name: str = ""
    kind: str = KIND_OPENCONNECT
    host: str = ""
    protocol: str = "gp"
    authgroup: str = ""
    config_path: str = ""
    username: str = ""
    extra_args: str = ""
    autoconnect: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Profile":
        return cls(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            host=row["host"],
            protocol=row["protocol"],
            authgroup=row["authgroup"],
            config_path=row["config_path"],
            username=row["username"],
            extra_args=row["extra_args"],
            autoconnect=bool(row["autoconnect"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @property
    def target(self) -> str:
        """One-line description of what this profile connects to."""
        if self.kind == KIND_OPENCONNECT:
            return f"{self.protocol}://{self.host}" if self.host else "(no host set)"
        return self.config_path or "(no config file set)"


class Store:
    """All database access goes through here."""

    def __init__(self, path: Path | None = None, box: SecretBox | None = None) -> None:
        ensure_dirs()
        self.path: Path = path or DB_PATH
        created = not self.path.exists()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self.conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()
        if created:
            # The file holds encrypted secrets, but there is no reason for
            # anyone else on the system to read it at all.
            self.path.chmod(0o600)
        self.box = box or SecretBox()

    # ---------------------------------------------------------------- profiles

    def list_profiles(self) -> list[Profile]:
        rows = self.conn.execute("SELECT * FROM profiles ORDER BY name COLLATE NOCASE")
        return [Profile.from_row(r) for r in rows]

    def get_profile(self, profile_id: int) -> Profile | None:
        row = self.conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return Profile.from_row(row) if row else None

    def save_profile(self, profile: Profile) -> Profile:
        """Insert or update, returning the profile with its id populated."""
        now = time.time()
        if profile.id is None:
            cur = self.conn.execute(
                """INSERT INTO profiles
                   (name, kind, host, protocol, authgroup, config_path, username,
                    extra_args, autoconnect, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    profile.name, profile.kind, profile.host, profile.protocol,
                    profile.authgroup, profile.config_path, profile.username,
                    profile.extra_args, int(profile.autoconnect), now, now,
                ),
            )
            self.conn.commit()
            return replace(profile, id=cur.lastrowid, created_at=now, updated_at=now)

        self.conn.execute(
            """UPDATE profiles SET
                 name = ?, kind = ?, host = ?, protocol = ?, authgroup = ?,
                 config_path = ?, username = ?, extra_args = ?, autoconnect = ?,
                 updated_at = ?
               WHERE id = ?""",
            (
                profile.name, profile.kind, profile.host, profile.protocol,
                profile.authgroup, profile.config_path, profile.username,
                profile.extra_args, int(profile.autoconnect), now, profile.id,
            ),
        )
        self.conn.commit()
        return replace(profile, updated_at=now)

    def delete_profile(self, profile_id: int) -> None:
        self.conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        self.conn.commit()

    # ------------------------------------------------------------- credentials

    def set_secret(self, profile_id: int, key: str, value: str | None) -> None:
        """Store a secret, or clear it when value is None/empty."""
        if not value:
            self.conn.execute(
                "DELETE FROM credentials WHERE profile_id = ? AND key = ?",
                (profile_id, key),
            )
        else:
            self.conn.execute(
                """INSERT INTO credentials (profile_id, key, value_enc)
                   VALUES (?, ?, ?)
                   ON CONFLICT (profile_id, key)
                   DO UPDATE SET value_enc = excluded.value_enc""",
                (profile_id, key, self.box.encrypt(value)),
            )
        self.conn.commit()

    def get_secret(self, profile_id: int, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value_enc FROM credentials WHERE profile_id = ? AND key = ?",
            (profile_id, key),
        ).fetchone()
        return self.box.decrypt(row["value_enc"]) if row else None

    def has_secret(self, profile_id: int, key: str) -> bool:
        """Check for a stored secret without decrypting it."""
        row = self.conn.execute(
            "SELECT 1 FROM credentials WHERE profile_id = ? AND key = ?",
            (profile_id, key),
        ).fetchone()
        return row is not None

    def clear_secrets(self, profile_id: int) -> None:
        self.conn.execute("DELETE FROM credentials WHERE profile_id = ?", (profile_id,))
        self.conn.commit()

    # ------------------------------------------------------------ app secrets

    def set_app_secret(self, key: str, value: str | None) -> None:
        if not value:
            self.conn.execute("DELETE FROM app_secrets WHERE key = ?", (key,))
        else:
            self.conn.execute(
                """INSERT INTO app_secrets (key, value_enc) VALUES (?, ?)
                   ON CONFLICT (key) DO UPDATE SET value_enc = excluded.value_enc""",
                (key, self.box.encrypt(value)),
            )
        self.conn.commit()

    def get_app_secret(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value_enc FROM app_secrets WHERE key = ?", (key,)
        ).fetchone()
        return self.box.decrypt(row["value_enc"]) if row else None

    def has_app_secret(self, key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM app_secrets WHERE key = ?", (key,)
        ).fetchone()
        return row is not None

    # --------------------------------------------------------------- settings

    def get_setting(self, key: str, default: str = "") -> str:
        """Read a UI preference. These are not secrets, so they stay in clear."""
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (f"setting.{key}",)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            """INSERT INTO meta (key, value) VALUES (?, ?)
               ON CONFLICT (key) DO UPDATE SET value = excluded.value""",
            (f"setting.{key}", value),
        )
        self.conn.commit()

    # ---------------------------------------------------------------- history

    def add_history(self, profile_id: int | None, event: str, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO history (profile_id, ts, event, detail) VALUES (?, ?, ?, ?)",
            (profile_id, time.time(), event, detail[:500]),
        )
        # Keep the log from growing without bound.
        self.conn.execute(
            "DELETE FROM history WHERE id NOT IN "
            "(SELECT id FROM history ORDER BY ts DESC LIMIT 500)"
        )
        self.conn.commit()

    def recent_history(self, limit: int = 50) -> Iterable[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM history ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()

    def close(self) -> None:
        self.conn.close()
